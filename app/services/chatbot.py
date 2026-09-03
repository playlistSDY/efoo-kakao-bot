from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from openai import OpenAI
from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.domain.meal_intent import infer_meal_intent, infer_meal_types, infer_target_date
from app.models import ChatSession, Meal, UserProfile
from app.services.chat_tools import CHAT_TOOLS, ChatToolExecutor
from app.services.prompt_loader import load_prompt_template


logger = logging.getLogger(__name__)
Presentation = Literal["simple_text", "basic_card", "carousel"]
ContextMode = Literal["new", "continuation", "recalled"]


class AgentTimeBudgetExceeded(RuntimeError):
    pass

KAKAO_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "kakao_agent_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "presentation": {"type": "string", "enum": ["simple_text", "basic_card", "carousel"]},
            "meal_ids": {"type": "array", "items": {"type": "integer"}},
            "meal_intent": {"type": "boolean"},
            "context_mode": {"type": "string", "enum": ["new", "continuation", "recalled"]},
            "quick_replies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "message_text": {"type": "string"},
                    },
                    "required": ["label", "message_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["message", "presentation", "meal_ids", "meal_intent", "context_mode", "quick_replies"],
        "additionalProperties": False,
    },
}


@dataclass
class AgentResult:
    answer: str
    presentation: Presentation = "simple_text"
    meals: list[Meal] = field(default_factory=list)
    meal_intent: bool = False
    context_mode: ContextMode = "new"
    quick_replies: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    agent_steps: list[str] = field(default_factory=list)


class MealChatAgent:
    def run(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> str:
        return self.run_result(db, user, session, user_text).answer

    def run_result(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> AgentResult:
        now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
        repo.update_profile_from_text(db, user, user_text)
        history = repo.get_recent_messages(db, session.id)
        current_message_id = _current_message_id(history, user_text)
        executor = ChatToolExecutor(
            db=db,
            now=now,
            user_id=user.id,
            excluded_message_ids={current_message_id} if current_message_id else set(),
        )
        if not settings.OPENAI_API_KEY:
            return self._fallback(user_text, now, executor, "OPENAI_API_KEY 미설정", refresh_if_empty=True)

        input_items: list = [
            {
                "role": "user",
                "content": self._build_user_context(user, user_text, now, history),
            }
        ]
        tool_calls: list[dict] = []
        deadline = monotonic() + settings.AGENT_TIME_BUDGET_SECONDS
        try:
            client = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.OPENAI_TIMEOUT_SECONDS,
                max_retries=0,
            )
            for round_number in range(1, settings.OPENAI_MAX_TOOL_ROUNDS + 1):
                self._check_deadline(deadline)
                request_options = {
                    "model": settings.OPENAI_MODEL,
                    "instructions": load_prompt_template("system.txt"),
                    "input": input_items,
                    "tools": CHAT_TOOLS,
                    "parallel_tool_calls": True,
                    "text": {"format": KAKAO_RESPONSE_FORMAT},
                    "safety_identifier": _safety_identifier(user.kakao_user_id),
                }
                if _supports_reasoning_options(settings.OPENAI_MODEL):
                    effort = (
                        settings.OPENAI_TOOL_REASONING_EFFORT
                        if round_number == 1
                        else settings.OPENAI_REASONING_EFFORT
                    )
                    request_options["reasoning"] = {"effort": effort}
                    request_options["text"]["verbosity"] = "low"
                response = client.responses.create(**request_options)
                input_items += response.output
                function_calls = [item for item in response.output if item.type == "function_call"]
                if not function_calls:
                    return self._parse_result(
                        response.output_text,
                        executor,
                        tool_calls,
                        round_number,
                        user_text,
                        bool(history[:-1] if current_message_id else history),
                    )

                for call in function_calls:
                    self._check_deadline(deadline)
                    arguments = _parse_tool_arguments(call.arguments)
                    output = executor.execute(call.name, arguments)
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": output,
                        }
                    )
                    tool_calls.append(
                        {
                            "round": round_number,
                            "name": call.name,
                            "args": arguments,
                            "result": json.loads(output),
                        }
                    )
                    self._check_deadline(deadline)
        except AgentTimeBudgetExceeded:
            logger.warning("에이전트 시간 예산 도달: budget=%ss", settings.AGENT_TIME_BUDGET_SECONDS)
            return self._fallback(user_text, now, executor, "시간 예산 도달", tool_calls)
        except Exception:
            logger.exception("Responses API 에이전트 실행 실패")
            return self._fallback(user_text, now, executor, "에이전트 실행 오류", tool_calls)

        logger.warning("에이전트가 최대 도구 라운드에 도달함: rounds=%s", settings.OPENAI_MAX_TOOL_ROUNDS)
        return self._fallback(user_text, now, executor, "최대 도구 라운드 도달", tool_calls)

    def run_debug(self, db: Session, user: UserProfile, session: ChatSession, user_text: str) -> dict:
        result = self.run_result(db, user, session, user_text)
        target_date = infer_target_date(user_text)
        return {
            "answer": result.answer,
            "presentation": result.presentation,
            "meals": result.meals,
            "quick_replies": result.quick_replies,
            "context_mode": result.context_mode,
            "lookup": {
                "target_date": str(target_date),
                "meal_count": len(result.meals),
                "meal_intent": result.meal_intent,
            },
            "target_date": str(target_date),
            "tool_calls": result.tool_calls,
            "agent_steps": result.agent_steps,
        }

    def _parse_result(
        self,
        output_text: str,
        executor: ChatToolExecutor,
        tool_calls: list[dict],
        round_number: int,
        user_text: str,
        has_prior_history: bool,
    ) -> AgentResult:
        payload = json.loads(output_text)
        meal_ids = [meal_id for meal_id in payload.get("meal_ids", []) if isinstance(meal_id, int)]
        meals = executor.selected_meals(meal_ids)
        presentation = _safe_presentation(str(payload.get("presentation", "simple_text")), meals)
        return AgentResult(
            answer=_non_empty_answer(str(payload.get("message", ""))),
            presentation=presentation,
            meals=meals,
            meal_intent=bool(payload.get("meal_intent")),
            context_mode=_safe_context_mode(
                str(payload.get("context_mode", "new")),
                tool_calls,
                user_text,
                has_prior_history,
            ),
            quick_replies=_sanitize_quick_replies(payload.get("quick_replies", [])),
            tool_calls=tool_calls,
            agent_steps=["remember_user", f"agent_tool_loop:{round_number}", "plan_kakao_response"],
        )

    def _fallback(
        self,
        user_text: str,
        now: datetime,
        executor: ChatToolExecutor,
        reason: str,
        tool_calls: list[dict] | None = None,
        refresh_if_empty: bool = False,
    ) -> AgentResult:
        meal_intent = infer_meal_intent(user_text)
        meals = list(executor.seen_meals.values())
        if meal_intent and not meals:
            target_date = infer_target_date(user_text, now)
            meal_types = infer_meal_types(user_text, now)
            result = executor.execute(
                "get_meals",
                {
                    "date": str(target_date),
                    "restaurant_codes": None,
                    "meal_types": meal_types,
                    "refresh": refresh_if_empty,
                },
            )
            tool_calls = (tool_calls or []) + [
                {
                    "round": "fallback",
                    "name": "get_meals",
                    "args": {
                        "date": str(target_date),
                        "restaurant_codes": None,
                        "meal_types": meal_types,
                        "refresh": refresh_if_empty,
                    },
                    "result": json.loads(result),
                }
            ]
            meals = list(executor.seen_meals.values())
        answer = _fallback_answer(meals, meal_intent)
        return AgentResult(
            answer=answer,
            presentation="carousel" if len(meals) > 1 else "basic_card" if meals else "simple_text",
            meals=meals[:10],
            meal_intent=meal_intent,
            tool_calls=tool_calls or [],
            agent_steps=["remember_user", f"fallback:{reason}"],
        )

    def _check_deadline(self, deadline: float) -> None:
        if monotonic() >= deadline:
            raise AgentTimeBudgetExceeded

    def _build_user_context(self, user: UserProfile, user_text: str, now: datetime, history: list) -> str:
        recent_history = history[-5:]
        if recent_history and recent_history[-1].role == "user" and recent_history[-1].content == user_text:
            recent_history = recent_history[:-1]
        history_text = "\n".join(f"{message.role}: {message.content}" for message in recent_history[-4:]) or "없음"
        profile = (
            f"알레르기={user.allergies or []}, 선호={user.preferences or []}, "
            f"비선호={user.dislikes or []}, 예산상한={user.budget_limit or '없음'}, 메모={user.extra_notes or '없음'}"
        )
        return (
            f"현재 서버 시각: {now.isoformat()} ({settings.APP_TIMEZONE})\n"
            f"사용자 기록: {profile}\n"
            "직전 대화 힌트(현재 질문이 후속 질문일 때만 사용):\n"
            f"{history_text}\n\n"
            f"이번 사용자 메시지: {user_text}"
        )


meal_chat_agent = MealChatAgent()


def _parse_tool_arguments(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _supports_reasoning_options(model: str) -> bool:
    normalized = model.lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def _current_message_id(history: list, user_text: str) -> int | None:
    if history and history[-1].role == "user" and history[-1].content == user_text:
        return history[-1].id
    return None


def _safe_context_mode(
    value: str,
    tool_calls: list[dict],
    user_text: str,
    has_prior_history: bool,
) -> ContextMode:
    recalled = any(call.get("name") == "recall_conversation" for call in tool_calls)
    if recalled:
        return "recalled"
    contextual_markers = (
        "그거",
        "그건",
        "그게",
        "그 메뉴",
        "그 식당",
        "거기",
        "방금",
        "아까",
        "그러면",
        "그럼",
        "다른 곳",
        "다른 메뉴",
        "더 싼",
        "더 비싼",
        "말한",
        "뭐였",
    )
    if has_prior_history and (value == "continuation" or any(marker in user_text for marker in contextual_markers)):
        return "continuation"
    return "new"


def _safe_presentation(value: str, meals: list[Meal]) -> Presentation:
    if not meals:
        return "simple_text"
    if value == "carousel" and len(meals) > 1:
        return "carousel"
    if value in {"basic_card", "carousel"}:
        return "basic_card"
    return "simple_text"


def _sanitize_quick_replies(items: object) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    replies = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        label = _limit_utf8(str(item.get("label", "")).strip(), 14)
        message_text = str(item.get("message_text", "")).strip()[:100]
        if label and message_text:
            replies.append({"label": label, "action": "message", "messageText": message_text})
    return replies


def _safety_identifier(kakao_user_id: str) -> str:
    return hashlib.sha256(f"efoo:{kakao_user_id}".encode()).hexdigest()[:64]


def _limit_utf8(text: str, max_bytes: int) -> str:
    result = ""
    for character in text:
        if len((result + character).encode("utf-8")) > max_bytes:
            break
        result += character
    return result.strip()


def _non_empty_answer(answer: str) -> str:
    return answer.strip() or "에푸가 답변을 만들지 못했어요.\n잠시 후 다시 물어봐 주세요."


def _fallback_answer(meals: list[Meal], meal_intent: bool) -> str:
    if not meal_intent:
        return "안녕하세요! 에푸예요."
    if not meals:
        return "요청한 날짜의 메뉴를\n아직 확인하지 못했어요."
    lines = ["확인된 메뉴예요.", ""]
    for meal in meals[:6]:
        restaurant = meal.restaurant.name if meal.restaurant else "식당"
        menu = ", ".join(meal.korean_name or []) or "메뉴 정보 없음"
        lines.extend([f"{restaurant} {meal.meal_type}", menu, ""])
    return "\n".join(lines).strip()
