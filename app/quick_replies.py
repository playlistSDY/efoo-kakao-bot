from __future__ import annotations

from datetime import date, datetime, timedelta

from app.entities import Meal


MAX_QUICK_REPLIES = 5


def build_quick_replies(
    utterance: str,
    target_date: date,
    meals: list[Meal],
    meal_intent: bool,
    now: datetime,
) -> list[dict]:
    if not meal_intent:
        return []

    suggestions: list[tuple[str, str]] = []
    date_text = _relative_date_text(target_date, now.date())

    meal_types = _ordered_unique([meal.meal_type for meal in meals])
    if meals and len(meal_types) > 1:
        for meal_type in meal_types:
            _add(suggestions, f"{meal_type}만 보기", f"{date_text} {meal_type} 메뉴")

    restaurant_names = _ordered_unique([meal.restaurant.name for meal in meals if meal.restaurant])
    if meals and restaurant_names:
        for restaurant_name in restaurant_names[:2]:
            _add(suggestions, f"{restaurant_name} 보기", f"{date_text} {restaurant_name} 메뉴")

    if meals:
        _add(suggestions, "운영시간 보기", f"{date_text} 식당 운영시간")
        _add(suggestions, "위치 보기", "식당 위치 알려줘")
    else:
        _add(suggestions, "오늘 메뉴", "오늘 메뉴 알려줘")
        _add(suggestions, "내일 메뉴", "내일 메뉴 알려줘")
        _add(suggestions, "점심 추천", "오늘 점심 추천해줘")
        _add(suggestions, "식당 위치", "식당 위치 알려줘")

    return [
        {
            "label": label,
            "action": "message",
            "messageText": message_text,
        }
        for label, message_text in suggestions[:MAX_QUICK_REPLIES]
    ]


def _add(suggestions: list[tuple[str, str]], label: str, message_text: str) -> None:
    label = _limit(label, 14)
    message_text = message_text.strip()
    if not label or not message_text:
        return
    if any(existing_label == label or existing_text == message_text for existing_label, existing_text in suggestions):
        return
    suggestions.append((label, message_text))


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
