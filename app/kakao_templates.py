from __future__ import annotations

from collections import defaultdict

from app.entities import Meal


SIMPLE_TEXT_LIMIT = 1000
CARD_TITLE_LIMIT = 40
CARD_DESCRIPTION_LIMIT = 80


def simple_text(text: str) -> dict:
    return {
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
    }


def build_kakao_response(answer: str, meals: list[Meal] | None = None) -> dict:
    meals = meals or []
    if not meals:
        return simple_text(answer)
    text = _build_text_with_full_menu(answer, meals)
    if len(meals) >= 2:
        return _carousel(text, meals[:10])
    if meals[0].image_url:
        return _basic_card(text, meals[0])
    return simple_text(text)


def _basic_card(answer: str, meal: Meal) -> dict:
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "basicCard": {
                        "title": _meal_title(meal),
                        "description": _meal_description(meal),
                        "thumbnail": {
                            "imageUrl": meal.image_url,
                        },
                    }
                },
                {
                    "simpleText": {
                        "text": _limit(answer, SIMPLE_TEXT_LIMIT),
                    }
                },
            ]
        },
    }


def _carousel(answer: str, meals: list[Meal]) -> dict:
    items = []
    for meal in meals:
        item = {
            "title": _limit(_meal_title(meal), CARD_TITLE_LIMIT),
            "description": _limit(_meal_card_description(meal), CARD_DESCRIPTION_LIMIT),
        }
        if meal.image_url:
            item["thumbnail"] = {"imageUrl": meal.image_url}
        items.append(item)

    return {
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
    }


def _meal_title(meal: Meal) -> str:
    restaurant = meal.restaurant.name if meal.restaurant else "식당"
    return f"{restaurant} {meal.meal_type}"


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


def _build_text_with_full_menu(answer: str, meals: list[Meal]) -> str:
    menu_text = _full_menu_text(meals)
    if not menu_text:
        return answer
    return _limit(f"{answer}\n\n메뉴 전체\n{menu_text}", SIMPLE_TEXT_LIMIT)


def _full_menu_text(meals: list[Meal]) -> str:
    grouped = defaultdict(list)
    for meal in meals:
        restaurant = meal.restaurant.name if meal.restaurant else "식당"
        grouped[(restaurant, meal.meal_type)].append(meal)

    lines = []
    for (restaurant, meal_type), group in grouped.items():
        lines.append(f"[{restaurant} {meal_type}]")
        for index, meal in enumerate(group, start=1):
            menu = _wrap_menu_items(meal.korean_name or [])
            price = f" ({meal.price})" if meal.price else ""
            lines.append(f"{index}. {menu}{price}")
    return "\n".join(lines)


def _wrap_menu_items(items: list[str]) -> str:
    if not items:
        return "메뉴명 없음"
    lines = []
    current = ""
    for item in items:
        candidate = item if not current else f"{current}, {item}"
        if len(candidate) <= 28:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = item
    if current:
        lines.append(current)
    return "\n   ".join(lines)


def _compact_menu(items: list[str], max_items: int) -> str:
    if not items:
        return "메뉴 정보 없음"
    return ", ".join(items[:max_items])


def _limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 12, 0)].rstrip() + "\n...더 있음"
