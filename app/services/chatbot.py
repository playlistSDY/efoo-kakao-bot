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
from app.domain.recommendation import format_recommendation_ranking, score_meals
from app.models import ChatSession, Meal, UserProfile
from app.services.meals.cache import ensure_fresh_meals
from app import repositories as repo
from app.domain.restaurants import format_open_status_context, format_restaurant_context
from app.services.prompt_builder import build_system_prompt, build_user_prompt, format_meals_for_prompt
from app.services.chat_tools import CHAT_TOOLS


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
    recommendation_ranking: NotRequired[str]
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
    recommendation_ranking: str
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
        scored_meals = []
        if meal_intent:
            ensure_fresh_meals(db, target_date, now)
            meals = repo.get_meals_flexible(db, target_date=target_date, meal_types=meal_types)
            scored_meals = score_meals(meals, state["user"], user_text, now, target_date)
            meals = [item.meal for item in scored_meals]
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
            "recommendation_ranking": (
                format_recommendation_ranking(scored_meals)
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
            SystemMessage(content=build_system_prompt()),
            HumanMessage(content=build_user_prompt(state, meal_context)),
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
