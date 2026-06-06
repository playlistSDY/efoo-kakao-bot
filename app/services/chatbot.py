from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session
from typing_extensions import NotRequired, TypedDict

from app.config import settings
from app.domain.meal_intent import format_target_date_text, infer_meal_intent, infer_meal_types, infer_target_date
from app.entities import ChatSession, Meal, UserProfile
from app.meal_cache import ensure_fresh_meals
from app import repositories as repo
from app.restaurant_info import format_open_status_context, format_restaurant_context
from app.services.prompt_builder import format_meals_for_prompt
from app.tools import CHAT_TOOLS


class AgentState(TypedDict):
    db: Session
    user: UserProfile
    session: ChatSession
    user_text: str
    now: datetime
    now_text: str
    target_date_obj: NotRequired[date]
    target_date: NotRequired[str]
    target_date_text: NotRequired[str]
    is_target_today: NotRequired[bool]
    meal_intent: NotRequired[bool]
    requested_meal_types: NotRequired[list[str] | None]
    meal_types: NotRequired[list[str]]
    meal_count: NotRequired[int]
    meals: NotRequired[list[Meal]]
    lookup: NotRequired[dict]
    profile_text: NotRequired[str]
    history_text: NotRequired[str]
    restaurant_context: NotRequired[str]
    open_status_context: NotRequired[str]
    meal_context: NotRequired[str]
    answer: NotRequired[str]
    tool_calls: NotRequired[list[dict]]
    agent_steps: NotRequired[list[str]]


class AgentStateUpdate(TypedDict, total=False):
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

    def _remember_user(self, state: AgentState) -> AgentStateUpdate:
        db = state["db"]
        user = state["user"]
        repo.update_profile_from_text(db, user, state["user_text"])
        return {
            "profile_text": self._profile_text(user),
            "agent_steps": state.get("agent_steps", []) + ["remember_user"],
        }

    def _resolve_lookup(self, state: AgentState) -> AgentStateUpdate:
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

    def _load_context(self, state: AgentState) -> AgentStateUpdate:
        recent = repo.get_recent_messages(state["db"], state["session"].id)
        return {
            "history_text": "\n".join(f"{m.role}: {m.content}" for m in recent),
            "restaurant_context": format_restaurant_context(),
            "open_status_context": format_open_status_context(state["now"]),
            "agent_steps": state.get("agent_steps", []) + ["load_context"],
        }

    def _generate(self, state: AgentState) -> AgentStateUpdate:
        meal_context = state.get("meal_context", "조회된 학식 데이터가 없습니다.")
        if not settings.OPENAI_API_KEY:
            return {
                "answer": (
                    "아직 OPENAI_API_KEY가 설정되지 않아서 AI 추천은 잠시 쉬고 있어요.\n\n"
                    f"현재 조회된 학식 정보:\n{meal_context}"
                ),
                "tool_calls": [],
                "agent_steps": state.get("agent_steps", []) + ["generate"],
            }

        llm = ChatOpenAI(api_key=lambda: settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL, temperature=0.4)
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
                    "현재 날짜/시간이 필요하면 제공된 도구를 호출한다. "
                    "식당 위치, 줄임말, 운영시간 질문은 제공된 식당 기본 정보를 기준으로 답한다. "
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
                    f"사용자 기록: {state.get('profile_text', '없음')}\n"
                    f"최근 대화:\n{state.get('history_text') or '없음'}\n\n"
                    f"식당 기본 정보:\n{state.get('restaurant_context', '없음')}\n\n"
                    f"오늘 현재 시각 기준 운영 상태:\n{state.get('open_status_context', '없음')}\n\n"
                    f"DB 학식 데이터:\n{meal_context}\n\n"
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

        return {
            "answer": _non_empty_answer(str(response.content), bool(state.get("meal_intent"))),
            "tool_calls": executed_tool_calls,
            "agent_steps": state.get("agent_steps", []) + ["generate"],
        }

    def _profile_text(self, user: UserProfile) -> str:
        return (
            f"알러지={user.allergies or []}, "
            f"선호={user.preferences or []}, "
            f"비선호={user.dislikes or []}, "
            f"예산상한={user.budget_limit or '없음'}, "
            f"메모={user.extra_notes or '없음'}"
        )


meal_chat_agent = MealChatAgent()


def _non_empty_answer(answer: str, meal_intent: bool) -> str:
    normalized = answer.strip()
    if normalized:
        return normalized
    if meal_intent:
        return "에푸가 답변을 정리하다가 잠깐 멈췄어요.\n아래 버튼으로 다시 물어봐 주세요."
    return "에푸가 답변을 만들지 못했어요.\n다시 한 번 말해 주세요."
