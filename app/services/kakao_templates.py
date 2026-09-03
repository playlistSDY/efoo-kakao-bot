from __future__ import annotations

from typing import Any

from app.models import Meal
from app.services.meals.image_cache import public_meal_image_url


SIMPLE_TEXT_LIMIT = 1000
CARD_TITLE_LIMIT = 40
CARD_DESCRIPTION_LIMIT = 80
EMPTY_TEXT_FALLBACK = "에푸가 답변을 만들지 못했어요.\n다시 한 번 말해 주세요."


def simple_text(text: str, quick_replies: list[dict] | None = None) -> dict:
    text = _safe_text(text)
    return _with_quick_replies(
        {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": _limit(text, SIMPLE_TEXT_LIMIT),
                        }
                    }
                ]
            },
        },
        quick_replies,
    )


def build_kakao_response(
    answer: str,
    meals: list[Meal] | None = None,
    quick_replies: list[dict] | None = None,
    presentation: str = "auto",
) -> dict:
    meals = meals or []
    if not meals:
        return simple_text(answer, quick_replies)
    if presentation == "simple_text":
        return simple_text(answer, quick_replies)
    if presentation == "carousel" or (presentation == "auto" and len(meals) >= 2):
        return _carousel(answer, meals[:10], quick_replies)
    return _basic_card(answer, meals[0], quick_replies)


def _basic_card(answer: str, meal: Meal, quick_replies: list[dict] | None = None) -> dict:
    answer = _safe_text(answer)
    card: dict[str, Any] = {
        "title": _limit(_meal_title(meal), CARD_TITLE_LIMIT),
        "description": _limit(_meal_description(meal), CARD_DESCRIPTION_LIMIT),
    }
    image_url = public_meal_image_url(meal.image_url)
    if image_url:
        card["thumbnail"] = {"imageUrl": image_url}
    return _with_quick_replies(
        {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "basicCard": card
                    },
                    {
                        "simpleText": {
                            "text": _limit(answer, SIMPLE_TEXT_LIMIT),
                        }
                    },
                ]
            },
        },
        quick_replies,
    )


def _carousel(answer: str, meals: list[Meal], quick_replies: list[dict] | None = None) -> dict:
    answer = _safe_text(answer)
    items = []
    for meal in meals:
        item: dict[str, Any] = {
            "title": _limit(_meal_title(meal), CARD_TITLE_LIMIT),
            "description": _limit(_meal_card_description(meal), CARD_DESCRIPTION_LIMIT),
        }
        image_url = public_meal_image_url(meal.image_url)
        if image_url:
            item["thumbnail"] = {"imageUrl": image_url}
        items.append(item)

    return _with_quick_replies(
        {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "carousel": {
                            "type": "basicCard",
                            "items": items,
                        }
                    },
                    {
                        "simpleText": {
                            "text": _limit(answer, SIMPLE_TEXT_LIMIT),
                        }
                    },
                ]
            },
        },
        quick_replies,
    )


def _with_quick_replies(response: dict, quick_replies: list[dict] | None) -> dict:
    if quick_replies:
        response["template"]["quickReplies"] = quick_replies[:5]
    return response


def _meal_title(meal: Meal) -> str:
    restaurant = meal.restaurant.name if meal.restaurant else "식당"
    return f"{restaurant} {meal.meal_type} ({meal.date.month}월 {meal.date.day}일)"


def _meal_description(meal: Meal) -> str:
    menu = ", ".join(meal.korean_name or [])
    price = f"\n가격: {meal.price}" if meal.price else ""
    tags = f"\n태그: {', '.join(meal.tags)}" if meal.tags else ""
    return f"{menu}{price}{tags}"


def _meal_card_description(meal: Meal) -> str:
    menu = _compact_menu(meal.korean_name or [], max_items=3)
    price = f" / {meal.price}" if meal.price else ""
    more_count = max(len(meal.korean_name or []) - 3, 0)
    more = f" 외 {more_count}개" if more_count else ""
    return f"{menu}{more}{price}"


def _compact_menu(items: list[str], max_items: int) -> str:
    if not items:
        return "메뉴 정보 없음"
    return ", ".join(items[:max_items])


def _limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 12, 0)].rstrip() + "\n...더 있음"


def _safe_text(text: str) -> str:
    return text.strip() or EMPTY_TEXT_FALLBACK
