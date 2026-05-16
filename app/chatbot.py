from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import TypedDict
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.config import settings
from app.entities import ChatSession, Meal, UserProfile
from app.meal_cache import ensure_fresh_meals
from app import repositories as repo
from app.restaurant_info import format_open_status_context, format_restaurant_context, meal_type_status
from app.tools import CHAT_TOOLS


WEEKDAY_TO_INDEX = {
    "월요일": 0,
    "월욜": 0,
    "화요일": 1,
    "화욜": 1,
    "수요일": 2,
    "수욜": 2,
    "목요일": 3,
    "목욜": 3,
    "금요일": 4,
    "금욜": 4,
    "토요일": 5,
    "토욜": 5,
    "일요일": 6,
    "일욜": 6,
}


def infer_target_date(text: str, now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if "모레" in text:
        return (now + timedelta(days=2)).date()
    if "내일" in text:
        return (now + timedelta(days=1)).date()
    if "어제" in text:
        return (now - timedelta(days=1)).date()
    explicit_date = _infer_explicit_date(text, now)
    if explicit_date:
        return explicit_date
    weekday_date = _infer_weekday_date(text, now)
    if weekday_date:
        return weekday_date
    return now.date()


def infer_meal_types(text: str, now: datetime | None = None) -> list[str] | None:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if "아침" in text or "조식" in text:
        return ["조식"]
    if "점심" in text or "중식" in text:
        return ["중식"]
    if "저녁" in text or "석식" in text:
        return ["석식"]

    target_date = infer_target_date(text, now)
    if target_date != now.date():
        return None

    if now.hour < 10:
        return ["조식", "중식"]
    if now.hour < 16:
        return ["중식"]
    return ["석식"]


def infer_meal_intent(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    meal_keywords = [
        "학식",
        "교식",
        "긱식",
        "창보",
        "창의",
        "창의인재",
        "기숙사식당",
        "학생식당",
        "교직원식당",
        "창업보육",
        "식당",
        "메뉴",
        "밥",
        "먹",
        "먹을까",
        "조식",
        "아침",
        "중식",
        "점심",
        "석식",
        "저녁",
        "배고",
        "식사",
        "운영시간",
        "몇시",
        "몇 시",
        "열어",
        "닫아",
        "마감",
    ]
    return any(keyword in normalized for keyword in meal_keywords)


def _infer_weekday_date(text: str, now: datetime) -> date | None:
    match = re.search(r"(지난|저번|이번|다음)?\s*(월요일|월욜|화요일|화욜|수요일|수욜|목요일|목욜|금요일|금욜|토요일|토욜|일요일|일욜)(?:날|에)?", text)
    if not match:
        return None

    modifier = match.group(1) or ""
    weekday_text = match.group(2)
    target_weekday = WEEKDAY_TO_INDEX[weekday_text]
    today_weekday = now.weekday()

    if modifier in {"지난", "저번"}:
        days_back = (today_weekday - target_weekday) % 7 or 7
        return (now - timedelta(days=days_back)).date()

    days_ahead = (target_weekday - today_weekday) % 7
    if modifier == "다음" and days_ahead == 0:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).date()


def _infer_explicit_date(text: str, now: datetime) -> date | None:
    month_day = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if month_day:
        month = int(month_day.group(1))
        day = int(month_day.group(2))
        try:
            return date(now.year, month, day)
        except ValueError:
            return None

    day_only = re.search(r"(?<!월\s)(\d{1,2})\s*일", text)
    if day_only:
        day = int(day_only.group(1))
        try:
            return date(now.year, now.month, day)
        except ValueError:
            return None

    return None


def load_meal_context(db: Session, text: str, now: datetime | None = None) -> tuple[date, list[str] | None, list[Meal]]:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    target_date = infer_target_date(text, now)
    meal_types = infer_meal_types(text, now)
    ensure_fresh_meals(db, target_date, now)
    return target_date, meal_types, repo.get_meals_flexible(db, target_date=target_date, meal_types=meal_types)


def format_meals_for_prompt(meals: list[Meal], now: datetime | None = None) -> str:
    if not meals:
        return "조회된 학식 데이터가 없습니다."
    lines = []
    for meal in meals:
        restaurant = meal.restaurant.name if meal.restaurant else f"식당#{meal.restaurant_id}"
        restaurant_code = meal.restaurant.code if meal.restaurant else ""
        meal_date = f"{meal.date.month}월 {meal.date.day}일"
        menu = ", ".join(meal.korean_name or [])
        tags = f" [{', '.join(meal.tags)}]" if meal.tags else ""
        price = f" / {meal.price}" if meal.price else ""
        status = ""
        if now and meal.date == now.date():
            status = f" / 오늘 현재상태: {meal_type_status(restaurant_code, meal.meal_type, now)}"
        lines.append(f"- {restaurant} {meal.meal_type}({meal_date}): {menu}{tags}{price}{status}")
    return "\n".join(lines)


def format_target_date_text(target_date: date) -> str:
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return f"{target_date.year}-{target_date.month:02d}-{target_date.day:02d} {weekdays[target_date.weekday()]}"


class AgentState(TypedDict, total=False):
    db: Session
    user: UserProfile
    session: ChatSession
    user_text: str
    now: datetime
    now_text: str
    target_date_obj: date
    target_date: str
    target_date_text: str
    is_target_today: bool
    meal_intent: bool
    requested_meal_types: list[str] | None
    meal_types: list[str]
    meal_count: int
    meals: list[Meal]
    lookup: dict
    profile_text: str
    history_text: str
    restaurant_context: str
    open_status_context: str
    meal_context: str
    answer: str
    tool_calls: list[dict]
    agent_steps: list[str]


class MealChatAgent:
    def __init__(self):
        self.graph = self._build_graph()

    def run(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> str:
        return self.run_debug(db, user, session, user_text)["answer"]

    def run_debug(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> dict:
        now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
        result = self.graph.invoke(
            {
                "db": db,
                "user": user,
                "session": session,
                "user_text": user_text,
                "now_text": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "now": now,
                "agent_steps": [],
            }
        )
        return {
            "answer": result["answer"],
            "lookup": result["lookup"],
            "tool_calls": result.get("tool_calls", []),
            "agent_steps": result.get("agent_steps", []),
            "target_date": result.get("target_date"),
            "meals": result.get("meals", []),
        }

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("remember_user", self._remember_user)
        graph.add_node("resolve_lookup", self._resolve_lookup)
        graph.add_node("load_context", self._load_context)
        graph.add_node("generate", self._generate)
        graph.set_entry_point("remember_user")
        graph.add_edge("remember_user", "resolve_lookup")
        graph.add_edge("resolve_lookup", "load_context")
        graph.add_edge("load_context", "generate")
        graph.add_edge("generate", END)
        return graph.compile()

    def _remember_user(self, state: AgentState) -> AgentState:
        db = state["db"]
        user = state["user"]
        repo.update_profile_from_text(db, user, state["user_text"])
        return {
            "profile_text": self._profile_text(user),
            "agent_steps": state.get("agent_steps", []) + ["remember_user"],
        }

    def _resolve_lookup(self, state: AgentState) -> AgentState:
        db = state["db"]
        now = state["now"]
        user_text = state["user_text"]
        target_date = infer_target_date(user_text, now)
        meal_types = infer_meal_types(user_text, now)
        meal_intent = infer_meal_intent(user_text)
        meals = []
        if meal_intent:
            ensure_fresh_meals(db, target_date, now)
            meals = repo.get_meals_flexible(db, target_date=target_date, meal_types=meal_types)
        lookup = {
            "target_date": str(target_date),
            "meal_types": meal_types,
            "meal_count": len(meals),
            "restaurants": sorted({meal.restaurant.code for meal in meals if meal.restaurant}),
            "meal_intent": meal_intent,
        }
        return {
            "target_date_obj": target_date,
            "target_date": str(target_date),
            "target_date_text": format_target_date_text(target_date),
            "is_target_today": target_date == now.date(),
            "meal_intent": meal_intent,
            "requested_meal_types": meal_types,
            "meal_types": meal_types or ["조식", "중식", "석식"],
            "meal_count": len(meals),
            "meals": meals,
            "lookup": lookup,
            "meal_context": (
                format_meals_for_prompt(meals, now)
                if meal_intent
                else "이번 사용자 메시지는 학식/식당/메뉴 질문으로 판단되지 않았습니다."
            ),
            "agent_steps": state.get("agent_steps", []) + ["resolve_lookup"],
        }

    def _load_context(self, state: AgentState) -> AgentState:
        recent = repo.get_recent_messages(state["db"], state["session"].id)
        return {
            "history_text": "\n".join(f"{m.role}: {m.content}" for m in recent),
            "restaurant_context": format_restaurant_context(),
            "open_status_context": format_open_status_context(state["now"]),
            "agent_steps": state.get("agent_steps", []) + ["load_context"],
        }

    def _generate(self, state: AgentState) -> AgentState:
        if not settings.OPENAI_API_KEY:
            state["answer"] = (
                "아직 OPENAI_API_KEY가 설정되지 않아서 AI 추천은 잠시 쉬고 있어요.\n\n"
                f"현재 조회된 학식 정보:\n{state['meal_context']}"
            )
            state["tool_calls"] = []
            state["agent_steps"] = state.get("agent_steps", []) + ["generate"]
            return state

        llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL, temperature=0.4)
        llm_with_tools = llm.bind_tools(CHAT_TOOLS)
        tool_map = {tool.name: tool for tool in CHAT_TOOLS}
        messages = [
            SystemMessage(
                content=(
                    "너는 대학교 학식 안내 및 추천 카카오톡 챗봇 '에푸'이다. "
                    "주 역할은 학식, 교내 식당, 메뉴, 운영시간, 위치 안내이다. "
                    "하지만 사용자가 학식, 식당, 메뉴, 밥, 식사, 추천, 운영시간, 위치와 관련 없는 말을 하면 학식 이야기로 억지로 연결하지 않는다. "
                    "학식 의도가 없는 질문에는 DB 학식 데이터를 언급하지 말고, 사용자가 물어본 내용에만 짧게 답한다. "
                    "잡담이나 인사에는 자연스럽게 짧게 답하고, 학식 추천을 먼저 제안하지 않는다. "
                    "학식이나 식당 질문일 때만 제공된 DB 학식 데이터 안에서 메뉴를 안내한다. "
                    "학식이나 식당 질문일 때는 사용자의 알러지, 취향, 예산, 현재 날짜와 시간을 반영해 추천한다. "
                    "메뉴 데이터가 없으면 없다고 말하고 임의 메뉴를 만들지 않는다. "
                    "사용자의 날짜 표현은 서버가 target_date로 변환해서 제공한다. "
                    "답변할 때는 이 target_date와 DB 학식 데이터의 날짜를 신뢰한다. "
                    "조회된 학식 데이터 개수가 1개 이상이면 절대로 데이터가 없다고 말하지 않는다. "
                    "조회 대상 식사가 조식, 중식, 석식이면 특정 식사 하나만 묻는 것이 아니라 해당 날짜 전체 후보를 보는 것이다. "
                    "현재 시각 기준 운영 종료, 운영 전, 운영 중 판단은 조회 대상 날짜가 오늘일 때만 적용한다. "
                    "조회 대상 날짜가 오늘이 아니면 현재 시각 때문에 이미 닫혔다고 말하지 않는다. "
                    "미래 날짜를 묻는 경우에는 해당 날짜의 메뉴 후보를 추천하고, 운영시간은 참고 정보로만 짧게 안내한다. "
                    "오늘 메뉴에서 현재 시간이 해당 식사의 운영 종료 이후라면 추천 전에 아쉽지만 지금은 운영이 끝났을 가능성이 크다고 알려준다. "
                    "오늘 메뉴가 운영 전이면 시작 시간을 알려주고 기다릴 수 있는지 안내한다. 오늘 메뉴가 운영 중이면 바로 이용 가능하다고 말한다. "
                    "현재 날짜/시간이나 날씨가 필요하면 제공된 도구를 호출한다. "
                    "식당 위치, 줄임말, 운영시간 질문은 제공된 식당 기본 정보를 기준으로 답한다. "
                    "날씨를 고려해 추천할 때는 비/기온/체감온도에 맞춰 이동 부담이나 따뜻한 메뉴 선호를 설명한다. "
                    "사용자가 특정 식당이나 특정 식사 시간의 메뉴를 물으면, 해당되는 메뉴 항목은 답변 본문에 빠짐없이 모두 포함한다. "
                    "메뉴 카드가 따로 붙더라도 카드에 의존하지 말고 텍스트 본문만 읽어도 전체 메뉴를 알 수 있게 작성한다. "
                    "여러 메뉴가 있으면 메뉴1, 메뉴2처럼 짧게 나누어 적는다. "
                    "말투는 친근한 AI 친구처럼 자연스럽게 한다. "
                    "다만 과장하거나 없는 정보를 만들지 말고, 확실하지 않은 내용은 조심스럽게 말한다. "
                    "카카오톡 답변이므로 한국어로 짧고 실용적으로 답한다. "
                    "카카오톡에서 읽기 쉽도록 답변은 줄바꿈을 적극 사용하고, 한 줄은 15자 이하가 되게 작성한다. "
                    "마크다운 문법은 사용하지 않는다. 굵게, 제목, 코드블록, 표, 목록 기호(*, **, -, #, `) 없이 일반 텍스트와 줄바꿈만 사용한다. "
                    "'궁금한 점이 있으면 언제든지 물어봐' 같은 추가 질문 유도 문구나 일반적인 마무리 인사는 쓰지 않는다. "
                    "답변은 사용자가 물어본 내용까지만 처리하고 끝낸다. "
                    "추천할 때는 한 줄 요약, 이유, 운영시간 주의사항 순서로 읽기 쉽게 답한다."
                )
            ),
            HumanMessage(
                content=(
                    f"기본 현재 시각: {state['now_text']}\n"
                    f"조회 대상 날짜: {state.get('target_date_text') or state.get('target_date', '알 수 없음')}\n"
                    f"조회 대상이 오늘인지: {'예' if state.get('is_target_today') else '아니오'}\n"
                    f"학식/식당 관련 의도인지: {'예' if state.get('meal_intent') else '아니오'}\n"
                    f"조회 대상 식사: {', '.join(state.get('meal_types', [])) or '알 수 없음'}\n"
                    f"조회된 학식 데이터 개수: {state.get('meal_count', 0)}\n"
                    f"사용자 기록: {state['profile_text']}\n"
                    f"최근 대화:\n{state['history_text'] or '없음'}\n\n"
                    f"식당 기본 정보:\n{state['restaurant_context']}\n\n"
                    f"오늘 현재 시각 기준 운영 상태:\n{state['open_status_context']}\n\n"
                    f"DB 학식 데이터:\n{state['meal_context']}\n\n"
                    f"사용자 메시지: {state['user_text']}"
                )
            ),
        ]

        response = llm_with_tools.invoke(messages)
        messages.append(response)
        executed_tool_calls = []
        for tool_call in getattr(response, "tool_calls", []) or []:
            selected_tool = tool_map.get(tool_call["name"])
            if not selected_tool:
                continue
            try:
                tool_result = selected_tool.invoke(tool_call.get("args") or {})
            except Exception as exc:
                tool_result = f"도구 실행 실패: {exc}"
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))
            executed_tool_calls.append(
                {
                    "name": tool_call["name"],
                    "args": tool_call.get("args") or {},
                    "result": str(tool_result),
                }
            )

        if getattr(response, "tool_calls", None):
            response = llm_with_tools.invoke(messages)

        state["answer"] = str(response.content)
        state["tool_calls"] = executed_tool_calls
        state["agent_steps"] = state.get("agent_steps", []) + ["generate"]
        return state

    def _profile_text(self, user: UserProfile) -> str:
        return (
            f"알러지={user.allergies or []}, "
            f"선호={user.preferences or []}, "
            f"비선호={user.dislikes or []}, "
            f"예산상한={user.budget_limit or '없음'}, "
            f"메모={user.extra_notes or '없음'}"
        )


meal_chat_agent = MealChatAgent()
