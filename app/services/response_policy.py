from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.entities import Meal


@dataclass(frozen=True)
class KakaoPresentationPlan:
    template: str
    attach_meal_cards: bool
    reason: str


def choose_kakao_presentation(utterance: str, target_date: date, meals: list[Meal]) -> KakaoPresentationPlan:
    if not meals:
        return KakaoPresentationPlan("simpleText", False, "no_meals")

    text = utterance.strip()
    asks_menu = _contains_any(
        text,
        [
            "메뉴",
            "학식",
            "밥",
            "식사",
            "먹",
            "추천",
            "조식",
            "아침",
            "중식",
            "점심",
            "석식",
            "저녁",
        ],
    )
    info_only = _contains_any(
        text,
        [
            "어디",
            "위치",
            "몇층",
            "몇 층",
            "운영시간",
            "몇시",
            "몇 시",
            "열어",
            "닫아",
            "마감",
            "줄임말",
        ],
    ) and not _contains_any(text, ["메뉴", "먹", "추천", "뭐"])

    if info_only:
        return KakaoPresentationPlan("simpleText", False, "restaurant_info_question")
    if not asks_menu:
        return KakaoPresentationPlan("simpleText", False, "not_menu_question")
    if any(meal.date != target_date for meal in meals):
        return KakaoPresentationPlan("simpleText", False, "meal_date_mismatch")
    if len(meals) >= 2:
        return KakaoPresentationPlan("carousel", True, "multiple_matching_meals")
    if meals[0].image_url:
        return KakaoPresentationPlan("basicCard", True, "single_meal_with_image")
    return KakaoPresentationPlan("simpleText", False, "single_meal_without_image")


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
