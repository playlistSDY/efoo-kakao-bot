from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.models import Meal
from app.domain.restaurants import meal_type_status
from app.services.prompt_loader import load_prompt_template


def build_system_prompt() -> str:
    return load_prompt_template("system.txt")


def build_user_prompt(state: Mapping[str, Any], meal_context: str) -> str:
    return load_prompt_template("user.txt").format(
        now_text=state["now_text"],
        target_date_text=state.get("target_date_text") or state.get("target_date", "알 수 없음"),
        is_target_today="예" if state.get("is_target_today") else "아니오",
        meal_intent="예" if state.get("meal_intent") else "아니오",
        meal_types=", ".join(state.get("meal_types", [])) or "알 수 없음",
        meal_count=state.get("meal_count", 0),
        profile_text=state.get("profile_text", "없음"),
        history_text=state.get("history_text") or "없음",
        restaurant_context=state.get("restaurant_context", "없음"),
        open_status_context=state.get("open_status_context", "없음"),
        meal_context=meal_context,
        user_text=state["user_text"],
    )


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
