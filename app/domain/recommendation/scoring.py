from __future__ import annotations

from datetime import date, datetime
import re

from app.domain.recommendation.types import RecommendationIntent, ScoreBreakdown, ScoredMeal
from app.domain.restaurants import DEFAULT_RESTAURANT_INFO, meal_type_status
from app.models import Meal, UserProfile


def score_meals(
    meals: list[Meal],
    user: UserProfile,
    user_text: str,
    now: datetime,
    target_date: date,
    recommendation_intent: RecommendationIntent | None = None,
) -> list[ScoredMeal]:
    scored = [_score_meal(meal, user, user_text, now, target_date, recommendation_intent) for meal in meals]
    return sorted(scored, key=lambda item: (item.score, _menu_text(item.meal)), reverse=True)


def sort_meals_by_score(
    meals: list[Meal],
    user: UserProfile,
    user_text: str,
    now: datetime,
    target_date: date,
    recommendation_intent: RecommendationIntent | None = None,
) -> list[Meal]:
    return [item.meal for item in score_meals(meals, user, user_text, now, target_date, recommendation_intent)]


def format_recommendation_ranking(scored_meals: list[ScoredMeal], limit: int = 3) -> str:
    if not scored_meals:
        return "추천 랭킹을 만들 학식 데이터가 없습니다."

    lines = []
    for rank, item in enumerate(scored_meals[:limit], start=1):
        meal = item.meal
        restaurant = meal.restaurant.name if meal.restaurant else "식당"
        menu = _compact_menu(meal.korean_name or [])
        lines.append(f"{rank}위. {restaurant} {meal.meal_type} - {item.score}점")
        lines.append(f"메뉴: {menu}")
        if item.reasons:
            lines.append(f"추천 이유: {', '.join(item.reasons[:3])}")
        if item.warnings:
            lines.append(f"주의: {', '.join(item.warnings[:2])}")
        lines.append("")
    return "\n".join(lines).strip()


def _score_meal(
    meal: Meal,
    user: UserProfile,
    user_text: str,
    now: datetime,
    target_date: date,
    recommendation_intent: RecommendationIntent | None,
) -> ScoredMeal:
    reasons: list[str] = []
    warnings: list[str] = []
    text = _normalized_text(" ".join(meal.korean_name or []) + " " + " ".join(meal.tags or []))

    preference = _keyword_score(text, user.preferences or [], 15, 35)
    if preference:
        reasons.append("선호 키워드와 맞음")

    intent = _intent_score(text, recommendation_intent, reasons, warnings)
    dislike = -_keyword_score(text, user.dislikes or [], 20, 35)
    if dislike:
        warnings.append("비선호 키워드가 포함될 수 있음")

    allergy_matches = _matched_keywords(text, user.allergies or [])
    allergy = -100 if allergy_matches else 0
    if allergy_matches:
        warnings.append(f"알러지 키워드 확인 필요({', '.join(allergy_matches[:3])})")

    budget_limit = user.budget_limit or (recommendation_intent.budget_limit if recommendation_intent else None) or _parse_budget_from_text(user_text)
    budget = _budget_score(_parse_price(meal.price), budget_limit, reasons, warnings)
    restaurant = _restaurant_score(meal, user_text, reasons)
    availability = _availability_score(meal, now, target_date, reasons, warnings)
    variety = _variety_score(meal, reasons)

    breakdown = ScoreBreakdown(
        preference=preference,
        intent=intent,
        dislike=dislike,
        allergy=allergy,
        budget=budget,
        restaurant=restaurant,
        availability=availability,
        variety=variety,
    )
    return ScoredMeal(meal=meal, score=breakdown.total, reasons=reasons, warnings=warnings, breakdown=breakdown)


def _keyword_score(text: str, keywords: list[str], per_match: int, max_score: int) -> int:
    matches = _matched_keywords(text, keywords)
    return min(len(matches) * per_match, max_score)


def _intent_score(
    text: str,
    recommendation_intent: RecommendationIntent | None,
    reasons: list[str],
    warnings: list[str],
) -> int:
    if not recommendation_intent or recommendation_intent.is_empty():
        return 0

    score = 0
    desired_food_matches = _matched_keywords(text, recommendation_intent.desired_foods + recommendation_intent.matching_keywords)
    if desired_food_matches:
        score += min(len(desired_food_matches) * 18, 45)
        reasons.append(f"요청한 음식 조건과 맞음({', '.join(desired_food_matches[:3])})")

    cuisine_matches = _matched_keywords(text, recommendation_intent.desired_cuisines)
    if cuisine_matches:
        score += min(len(cuisine_matches) * 12, 24)
        reasons.append(f"요청한 음식 종류와 가까움({', '.join(cuisine_matches[:2])})")

    trait_matches = _matched_keywords(text, recommendation_intent.desired_traits)
    if trait_matches:
        score += min(len(trait_matches) * 8, 24)
        reasons.append(f"원하는 느낌과 맞음({', '.join(trait_matches[:3])})")

    avoid_food_matches = _matched_keywords(text, recommendation_intent.avoid_foods)
    if avoid_food_matches:
        score -= min(len(avoid_food_matches) * 25, 45)
        warnings.append(f"피하고 싶은 음식일 수 있음({', '.join(avoid_food_matches[:3])})")

    avoid_trait_matches = _matched_keywords(text, recommendation_intent.avoid_traits)
    if avoid_trait_matches:
        score -= min(len(avoid_trait_matches) * 18, 35)
        warnings.append(f"피하고 싶은 특성이 있을 수 있음({', '.join(avoid_trait_matches[:3])})")

    return score


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    matches = []
    for keyword in keywords:
        normalized = _normalized_text(keyword)
        if normalized and normalized in text:
            matches.append(keyword)
    return matches


def _budget_score(price: int | None, budget_limit: int | None, reasons: list[str], warnings: list[str]) -> int:
    if not price or not budget_limit:
        return 0
    if price <= budget_limit:
        reasons.append("예산 이하")
        return 10
    if price <= budget_limit + 1000:
        warnings.append("예산을 조금 넘음")
        return -10
    warnings.append("예산 초과")
    return -25


def _restaurant_score(meal: Meal, user_text: str, reasons: list[str]) -> int:
    if not meal.restaurant:
        return 0
    info = DEFAULT_RESTAURANT_INFO.get(meal.restaurant.code, {})
    names = [meal.restaurant.name, *info.get("aliases", [])]
    if any(name and name in user_text for name in names):
        reasons.append("요청한 식당과 일치")
        return 25
    return 0


def _availability_score(meal: Meal, now: datetime, target_date: date, reasons: list[str], warnings: list[str]) -> int:
    if meal.date != target_date or target_date != now.date() or not meal.restaurant:
        return 0
    status = meal_type_status(meal.restaurant.code, meal.meal_type, now)
    if "운영 중" in status:
        reasons.append("현재 운영 중")
        return 10
    if "운영 전" in status:
        reasons.append("오늘 운영 예정")
        return 3
    if "운영 종료" in status:
        warnings.append("오늘 운영 종료 가능성")
        return -20
    return 0


def _variety_score(meal: Meal, reasons: list[str]) -> int:
    count = len(meal.korean_name or [])
    if count >= 5:
        reasons.append("구성이 다양함")
        return 8
    if count >= 3:
        return 5
    return 0


def _parse_price(price: str | None) -> int | None:
    if not price:
        return None
    digits = re.sub(r"[^0-9]", "", price)
    return int(digits) if digits else None


def _parse_budget_from_text(text: str) -> int | None:
    match = re.search(r"(\d{1,6})\s*(천원|만원|원)", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "만원":
        return amount * 10000
    if unit == "천원":
        return amount * 1000
    return amount


def _normalized_text(text: str) -> str:
    normalized = re.sub(r"\s+", "", text.lower())
    replacements = {
        "돈까스": "돈가스",
        "돈카츠": "돈가스",
        "돈가츠": "돈가스",
        "카츠": "가스",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def _menu_text(meal: Meal) -> str:
    return " ".join(meal.korean_name or [])


def _compact_menu(items: list[str], max_items: int = 4) -> str:
    if not items:
        return "메뉴 정보 없음"
    menu = ", ".join(items[:max_items])
    if len(items) > max_items:
        menu += f" 외 {len(items) - max_items}개"
    return menu
