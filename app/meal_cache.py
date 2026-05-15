from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.entities import MealFetchLog
from app.meal_fetcher import meal_fetcher


MEAL_CACHE_TTL = timedelta(minutes=30)


def ensure_fresh_meals(db: Session, target_date: date, now: datetime) -> dict:
    refreshed = []
    reused = []

    for restaurant_code, restaurant_name in settings.RESTAURANT_CODES.items():
        restaurant = repo.get_or_create_restaurant(db, restaurant_code, restaurant_name)
        fetch_log = db.scalar(
            select(MealFetchLog).where(
                MealFetchLog.restaurant_id == restaurant.id,
                MealFetchLog.date == target_date,
                MealFetchLog.status == "success",
            )
        )
        if fetch_log and now - _normalize_datetime(fetch_log.fetched_at, now) < MEAL_CACHE_TTL:
            reused.append(restaurant_code)
            continue

        meal_fetcher.fetch_and_store_for_date(db, target_date, [restaurant_code])
        refreshed.append(restaurant_code)

    return {
        "target_date": str(target_date),
        "ttl_minutes": int(MEAL_CACHE_TTL.total_seconds() // 60),
        "reused": reused,
        "refreshed": refreshed,
    }


def _normalize_datetime(value: datetime, now: datetime) -> datetime:
    if value.tzinfo is None and now.tzinfo is not None:
        return value.replace(tzinfo=now.tzinfo)
    return value
