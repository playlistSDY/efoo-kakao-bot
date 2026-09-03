from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.services.meals.cache import MEAL_CACHE_TTL
from app.services.meals.image_cache import meal_image_cache


router = APIRouter()


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "model": settings.OPENAI_MODEL,
        "agent_time_budget_seconds": settings.AGENT_TIME_BUDGET_SECONDS,
        "meal_cache_ttl_minutes": int(MEAL_CACHE_TTL.total_seconds() // 60),
        "image_cache_enabled": meal_image_cache.enabled,
    }
