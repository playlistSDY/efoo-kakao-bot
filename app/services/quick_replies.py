from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.models import Meal


MAX_QUICK_REPLIES = 5
MAX_LABEL_LENGTH = 14
MAX_LABEL_BYTES = 14
MAX_MESSAGE_LENGTH = 100
logger = logging.getLogger(__name__)
QuickReply = dict[str, str]


def build_quick_replies(
    utterance: str,
    target_date: date,
    meals: list[Meal],
    meal_intent: bool,
    now: datetime,
    answer: str = "",
) -> list[QuickReply]:
    if not meal_intent:
        return []

    if settings.ENABLE_LLM_QUICK_REPLIES:
        llm_replies = _build_llm_quick_replies(utterance, target_date, meals, now, answer)
        if llm_replies:
            return llm_replies

    return _fallback_quick_replies(utterance, target_date, meals, now)


def _build_llm_quick_replies(
    utterance: str,
    target_date: date,
    meals: list[Meal],
    now: datetime,
    answer: str,
) -> list[QuickReply]:
    if not settings.OPENAI_API_KEY:
        return []

    try:
        llm = ChatOpenAI(api_key=lambda: settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL, temperature=0.2)
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "너는 카카오톡 챗봇의 quickReplies 후보를 만드는 도우미다. "
                        "사용자가 다음에 누르면 좋을 연계 질문을 1개에서 5개 만든다. "
                        "반드시 JSON 배열만 반환한다. 설명 문장, 마크다운, 코드블록은 쓰지 않는다. "
                        "각 항목은 label, messageText 필드만 가진다. "
                        "label은 한국어 4자 이하로 매우 짧게 쓴다. "
                        f"messageText는 {MAX_MESSAGE_LENGTH}자 이하의 자연스러운 사용자 질문으로 쓴다. "
                        "카카오 action 값은 서버가 붙이므로 만들지 않는다. "
                        "이미 사용자가 물어본 문장과 같은 질문은 피한다. "
                        "학식, 식당, 메뉴, 운영시간, 위치와 직접 관련된 후속 질문만 만든다. "
                        "날씨 관련 질문은 만들지 않는다. "
                        "제공된 식당명, 식사명, 날짜 범위 안에서만 질문을 만든다."
                    )
                ),
                HumanMessage(
                    content=(
                        f"현재 시각: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                        f"조회 날짜: {_relative_date_text(target_date, now.date())} ({target_date})\n"
                        f"사용자 메시지: {utterance}\n"
                        f"챗봇 답변: {answer or '없음'}\n"
                        f"조회된 메뉴 요약:\n{_meal_summary(meals)}"
                    )
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
        return _sanitize_quick_replies(_parse_llm_payload(content))
    except Exception:
        logger.exception("LLM quickReplies 생성 실패, 규칙 기반 fallback 사용")
        return []


def _fallback_quick_replies(utterance: str, target_date: date, meals: list[Meal], now: datetime) -> list[QuickReply]:
    suggestions: list[tuple[str, str]] = []
    date_text = _relative_date_text(target_date, now.date())

    meal_types = _ordered_unique([meal.meal_type for meal in meals])
    if meals and len(meal_types) > 1:
        for meal_type in meal_types:
            _add(suggestions, meal_type, f"{date_text} {meal_type} 메뉴")

    restaurant_names = _ordered_unique([meal.restaurant.name for meal in meals if meal.restaurant])
    if meals and restaurant_names:
        for restaurant_name in restaurant_names[:2]:
            _add(suggestions, _short_restaurant_label(restaurant_name), f"{date_text} {restaurant_name} 메뉴")

    if meals:
        _add(suggestions, "운영시간", f"{date_text} 식당 운영시간")
        _add(suggestions, "위치", "식당 위치 알려줘")
    else:
        _add(suggestions, "오늘", "오늘 메뉴 알려줘")
        _add(suggestions, "내일", "내일 메뉴 알려줘")
        _add(suggestions, "점심", "오늘 점심 추천해줘")
        _add(suggestions, "위치", "식당 위치 알려줘")

    return _format_quick_replies(suggestions)


def _parse_llm_payload(content: str) -> list[Mapping[str, Any]]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`").strip()
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()

    payload: Any = json.loads(normalized)
    if isinstance(payload, dict):
        payload = payload.get("quick_replies") or payload.get("quickReplies") or payload.get("items") or []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _sanitize_quick_replies(items: list[Mapping[str, Any]]) -> list[QuickReply]:
    suggestions: list[tuple[str, str]] = []
    for item in items:
        label = str(item.get("label") or "").strip()
        message_text = str(item.get("messageText") or item.get("message") or "").strip()
        if _has_blocked_topic(label) or _has_blocked_topic(message_text):
            continue
        _add(suggestions, label, message_text)
    return _format_quick_replies(suggestions)


def _format_quick_replies(suggestions: list[tuple[str, str]]) -> list[QuickReply]:
    return [
        {
            "label": label,
            "action": "message",
            "messageText": message_text,
        }
        for label, message_text in suggestions[:MAX_QUICK_REPLIES]
    ]


def _add(suggestions: list[tuple[str, str]], label: str, message_text: str) -> None:
    label = _limit_bytes(_limit(label.strip(), MAX_LABEL_LENGTH), MAX_LABEL_BYTES)
    message_text = _limit(message_text.strip(), MAX_MESSAGE_LENGTH)
    if not label or not message_text:
        return
    if any(existing_label == label or existing_text == message_text for existing_label, existing_text in suggestions):
        return
    suggestions.append((label, message_text))


def _short_restaurant_label(restaurant_name: str) -> str:
    mapping = {
        "학생식당": "학식",
        "교직원식당": "교식",
        "창의인재원식당": "창의",
        "창업보육센터": "창보",
    }
    return mapping.get(restaurant_name, restaurant_name.replace("식당", "").replace("센터", "")[:4])


def _has_blocked_topic(text: str) -> bool:
    return "날씨" in text


def _meal_summary(meals: list[Meal]) -> str:
    if not meals:
        return "조회된 메뉴 없음"
    lines = []
    for meal in meals[:12]:
        restaurant = meal.restaurant.name if meal.restaurant else "식당"
        menu = ", ".join(meal.korean_name or []) or "메뉴 정보 없음"
        price = f" / {meal.price}" if meal.price else ""
        lines.append(f"- {restaurant} {meal.meal_type}: {menu}{price}")
    return "\n".join(lines)


def _ordered_unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    order = {"조식": 0, "중식": 1, "석식": 2}
    return sorted(result, key=lambda value: order.get(value, 99))


def _relative_date_text(target_date: date, today: date) -> str:
    if target_date == today:
        return "오늘"
    if target_date == today + timedelta(days=1):
        return "내일"
    if target_date == today + timedelta(days=2):
        return "모레"
    return f"{target_date.month}월 {target_date.day}일"


def _limit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip()


def _limit_bytes(text: str, limit: int) -> str:
    result = ""
    used = 0
    for char in text:
        size = len(char.encode("utf-8"))
        if used + size > limit:
            break
        result += char
        used += size
    return result.rstrip()
