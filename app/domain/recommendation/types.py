from __future__ import annotations

from dataclasses import dataclass

from app.models import Meal


@dataclass(frozen=True)
class ScoreBreakdown:
    base: int = 50
    preference: int = 0
    intent: int = 0
    dislike: int = 0
    allergy: int = 0
    budget: int = 0
    restaurant: int = 0
    availability: int = 0
    variety: int = 0

    @property
    def total(self) -> int:
        return (
            self.base
            + self.preference
            + self.intent
            + self.dislike
            + self.allergy
            + self.budget
            + self.restaurant
            + self.availability
            + self.variety
        )


@dataclass(frozen=True)
class ScoredMeal:
    meal: Meal
    score: int
    reasons: list[str]
    warnings: list[str]
    breakdown: ScoreBreakdown


@dataclass(frozen=True)
class RecommendationIntent:
    desired_foods: list[str]
    desired_cuisines: list[str]
    desired_traits: list[str]
    avoid_foods: list[str]
    avoid_traits: list[str]
    matching_keywords: list[str]
    budget_limit: int | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.desired_foods,
                self.desired_cuisines,
                self.desired_traits,
                self.avoid_foods,
                self.avoid_traits,
                self.matching_keywords,
                self.budget_limit,
            ]
        )
