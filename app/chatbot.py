from __future__ import annotations

from datetime import date, datetime, timedelta
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


def infer_target_date(text: str, now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if "모레" in text:
        return (now + timedelta(days=2)).date()
    if "내일" in text:
        return (now + timedelta(days=1)).date()
    if "어제" in text:
        return (now - timedelta(days=1)).date()
    return now.date()


def infer_meal_types(text: str, now: datetime | None = None) -> list[str] | None:
    if "아침" in text or "조식" in text:
        return ["조식"]
    if "점심" in text or "중식" in text:
        return ["중식"]
    if "저녁" in text or "석식" in text:
        return ["석식"]
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if now.hour < 10:
        return ["조식", "중식"]
    if now.hour < 16:
        return ["중식"]
    return ["석식"]


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
        menu = ", ".join(meal.korean_name or [])
        tags = f" [{', '.join(meal.tags)}]" if meal.tags else ""
        price = f" / {meal.price}" if meal.price else ""
        status = f" / 현재상태: {meal_type_status(restaurant_code, meal.meal_type, now)}" if now else ""
        lines.append(f"- {restaurant} {meal.meal_type}: {menu}{tags}{price}{status}")
    return "\n".join(lines)


class AgentState(TypedDict, total=False):
    user_text: str
    now_text: str
    profile_text: str
    history_text: str
    restaurant_context: str
    open_status_context: str
    meal_context: str
    answer: str


class MealChatAgent:
    def __init__(self):
        self.graph = self._build_graph()

    def run(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> str:
        now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
        repo.update_profile_from_text(db, user, user_text)
        _, _, meals = load_meal_context(db, user_text, now)
        recent = repo.get_recent_messages(db, session.id)
        result = self.graph.invoke(
            {
                "user_text": user_text,
                "now_text": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "profile_text": self._profile_text(user),
                "history_text": "\n".join(f"{m.role}: {m.content}" for m in recent),
                "restaurant_context": format_restaurant_context(),
                "open_status_context": format_open_status_context(now),
                "meal_context": format_meals_for_prompt(meals, now),
            }
        )
        return result["answer"]

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("generate", self._generate)
        graph.set_entry_point("generate")
        graph.add_edge("generate", END)
        return graph.compile()

    def _generate(self, state: AgentState) -> AgentState:
        if not settings.OPENAI_API_KEY:
            state["answer"] = (
                "아직 OPENAI_API_KEY가 설정되지 않아서 AI 추천은 잠시 쉬고 있어요.\n\n"
                f"현재 조회된 학식 정보:\n{state['meal_context']}"
            )
            return state

        llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL, temperature=0.4)
        llm_with_tools = llm.bind_tools(CHAT_TOOLS)
        tool_map = {tool.name: tool for tool in CHAT_TOOLS}
        messages = [
            SystemMessage(
                content=(
                    "너는 대학교 학식 안내 및 추천 카카오톡 챗봇 '에푸'이다. "
                    "반드시 제공된 DB 학식 데이터 안에서만 메뉴를 안내한다. "
                    "사용자의 알러지, 취향, 예산, 현재 날짜와 시간을 반영해 추천한다. "
                    "메뉴 데이터가 없으면 없다고 말하고 임의 메뉴를 만들지 않는다. "
                    "현재 시간이 해당 식사의 운영 종료 이후라면 추천 전에 아쉽지만 지금은 운영이 끝났을 가능성이 크다고 알려준다. "
                    "운영 전이면 시작 시간을 알려주고 기다릴 수 있는지 안내한다. 운영 중이면 바로 이용 가능하다고 말한다. "
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
                    "추천할 때는 한 줄 요약, 이유, 운영시간 주의사항 순서로 읽기 쉽게 답한다."
                )
            ),
            HumanMessage(
                content=(
                    f"기본 현재 시각: {state['now_text']}\n"
                    f"사용자 기록: {state['profile_text']}\n"
                    f"최근 대화:\n{state['history_text'] or '없음'}\n\n"
                    f"식당 기본 정보:\n{state['restaurant_context']}\n\n"
                    f"현재 시각 기준 운영 상태:\n{state['open_status_context']}\n\n"
                    f"DB 학식 데이터:\n{state['meal_context']}\n\n"
                    f"사용자 메시지: {state['user_text']}"
                )
            ),
        ]

        response = llm_with_tools.invoke(messages)
        messages.append(response)
        for tool_call in getattr(response, "tool_calls", []) or []:
            selected_tool = tool_map.get(tool_call["name"])
            if not selected_tool:
                continue
            try:
                tool_result = selected_tool.invoke(tool_call.get("args") or {})
            except Exception as exc:
                tool_result = f"도구 실행 실패: {exc}"
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

        if getattr(response, "tool_calls", None):
            response = llm_with_tools.invoke(messages)

        state["answer"] = str(response.content)
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
