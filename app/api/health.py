from __future__ import annotations

from fastapi import APIRouter

from app.config import settings


router = APIRouter()


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "model": settings.OPENAI_MODEL,
        "agent_time_budget_seconds": settings.AGENT_TIME_BUDGET_SECONDS,
    }
