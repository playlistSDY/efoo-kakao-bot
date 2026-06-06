from __future__ import annotations

from datetime import datetime

from app.entities import Meal
from app.restaurant_info import meal_type_status


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
