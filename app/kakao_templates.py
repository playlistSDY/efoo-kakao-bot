from __future__ import annotations

from app.entities import Meal


def simple_text(text: str) -> dict:
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text[:1000],
                    }
                }
            ]
        },
    }


def build_kakao_response(answer: str, meals: list[Meal] | None = None) -> dict:
    meals = meals or []
    if not meals:
        return simple_text(answer)
    if len(meals) >= 2:
        return _carousel(answer, meals[:10])
    if meals[0].image_url:
        return _basic_card(answer, meals[0])
    return simple_text(answer)


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
                        "text": answer[:1000],
                    }
                },
            ]
        },
    }


def _carousel(answer: str, meals: list[Meal]) -> dict:
    items = []
    for meal in meals:
        item = {
            "title": _meal_title(meal)[:40],
            "description": _meal_description(meal)[:80],
        }
        if meal.image_url:
            item["thumbnail"] = {"imageUrl": meal.image_url}
        items.append(item)

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": answer[:1000],
                    }
                },
                {
                    "carousel": {
                        "type": "basicCard",
                        "items": items,
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
