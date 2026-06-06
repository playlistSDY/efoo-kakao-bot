from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.domain.meal_intent import format_target_date_text, infer_meal_intent, infer_meal_types, infer_target_date
from app.entities import Meal
from app.services.kakao_templates import build_kakao_response
from app.services.quick_replies import build_quick_replies
from app.services.response_policy import choose_kakao_presentation


def create_fast_sync_response(db: Session, kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, raw_payload)

    target_date = infer_target_date(utterance, now)
    meal_types = infer_meal_types(utterance, now)
    meal_intent = infer_meal_intent(utterance)
    meals = repo.get_meals_flexible(db, target_date=target_date, meal_types=meal_types) if meal_intent else []
    answer = _build_fast_answer(utterance, target_date, meal_types, meal_intent, meals)
    repo.add_message(db, session.id, "assistant", answer, {"source": "fast-sync"})

    presentation = choose_kakao_presentation(utterance, target_date, meals)
    quick_replies = build_quick_replies(utterance, target_date, meals, meal_intent, now, answer)
    return build_kakao_response(answer, meals if presentation.attach_meal_cards else [], quick_replies)


def _build_fast_answer(
    utterance: str,
    target_date: date,
    meal_types: list[str] | None,
    meal_intent: bool,
    meals: list[Meal],
) -> str:
    if not meal_intent:
        return (
            "에푸는 학식 메뉴와\n"
            "교내 식당 정보를\n"
            "도와줄 수 있어요.\n\n"
            "예를 들면\n"
            "오늘 점심 메뉴 알려줘\n"
            "처럼 물어봐 주세요."
        )

    target_text = format_target_date_text(target_date)
    meal_type_text = ", ".join(meal_types) if meal_types else "전체 식사"
    if not meals:
        return (
            f"{target_text}\n"
            f"{meal_type_text} 메뉴는\n"
            "아직 저장된 정보가 없어요.\n\n"
            "잠시 후 다시 물어보면\n"
            "에푸가 새로 확인해볼게요."
        )

    grouped = _group_meals_by_restaurant(meals)
    lines = [
        f"{target_text}",
        f"{meal_type_text} 메뉴예요.",
        "",
    ]
    for restaurant_name, restaurant_meals in grouped[:4]:
        lines.append(restaurant_name)
        for index, meal in enumerate(restaurant_meals[:2], start=1):
            menu = _compact_menu(meal.korean_name or [])
            price = f" / {meal.price}" if meal.price else ""
            label = "메뉴" if len(restaurant_meals) == 1 else f"메뉴{index}"
            lines.append(f"{label}: {menu}{price}")
        lines.append("")

    lines.append("더 자세한 추천은")
    lines.append("한 번 더 물어보면")
    lines.append("에푸가 이어서 볼게요.")
    return "\n".join(lines).strip()


def _group_meals_by_restaurant(meals: list[Meal]) -> list[tuple[str, list[Meal]]]:
    grouped: dict[str, list[Meal]] = {}
    for meal in meals:
        restaurant_name = meal.restaurant.name if meal.restaurant else "식당"
        grouped.setdefault(restaurant_name, []).append(meal)
    return list(grouped.items())


def _compact_menu(items: list[str], max_items: int = 4) -> str:
    if not items:
        return "메뉴 정보 없음"
    menu = ", ".join(items[:max_items])
    if len(items) > max_items:
        menu += f" 외 {len(items) - max_items}개"
    return menu
