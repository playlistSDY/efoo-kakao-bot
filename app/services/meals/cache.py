from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.models import MealFetchLog
from app.services.meals.fetch_locks import meal_fetch_lock
from app.services.meals.fetcher import meal_fetcher


MEAL_CACHE_TTL = timedelta(minutes=30)
logger = logging.getLogger(__name__)


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
            logger.info("학식 캐시 사용: restaurant=%s date=%s fetched_at=%s", restaurant_code, target_date, fetch_log.fetched_at)
            continue

        with meal_fetch_lock(restaurant_code, target_date):
            fetch_log = db.scalar(
                select(MealFetchLog).where(
                    MealFetchLog.restaurant_id == restaurant.id,
                    MealFetchLog.date == target_date,
                    MealFetchLog.status == "success",
                )
            )
            if fetch_log and now - _normalize_datetime(fetch_log.fetched_at, now) < MEAL_CACHE_TTL:
                reused.append(restaurant_code)
                logger.info(
                    "학식 캐시 대기 후 사용: restaurant=%s date=%s fetched_at=%s",
                    restaurant_code,
                    target_date,
                    fetch_log.fetched_at,
                )
                continue

            logger.info("학식 캐시 갱신: restaurant=%s date=%s", restaurant_code, target_date)
            try:
                meal_fetcher.fetch_and_store_for_date(db, target_date, [restaurant_code])
                refreshed.append(restaurant_code)
            except Exception:
                logger.exception(
                    "학식 캐시 갱신 실패, 기존 DB 데이터로 진행: restaurant=%s date=%s",
                    restaurant_code,
                    target_date,
                )
                reused.append(restaurant_code)

    return {
        "target_date": str(target_date),
        "ttl_minutes": int(MEAL_CACHE_TTL.total_seconds() // 60),
        "reused": reused,
        "refreshed": refreshed,
    }


def get_meal_cache_status(db: Session, target_date: date, now: datetime) -> dict:
    restaurants = {}
    for restaurant_code, restaurant_name in settings.RESTAURANT_CODES.items():
        restaurant = repo.get_or_create_restaurant(db, restaurant_code, restaurant_name)
        fetch_log = db.scalar(
            select(MealFetchLog).where(
                MealFetchLog.restaurant_id == restaurant.id,
                MealFetchLog.date == target_date,
                MealFetchLog.status == "success",
            )
        )
        if not fetch_log:
            restaurants[restaurant_code] = {"fresh": False, "fetched_at": None}
            continue
        fetched_at = _normalize_datetime(fetch_log.fetched_at, now)
        age_seconds = max((now - fetched_at).total_seconds(), 0)
        restaurants[restaurant_code] = {
            "fresh": age_seconds < MEAL_CACHE_TTL.total_seconds(),
            "fetched_at": fetch_log.fetched_at.isoformat(),
            "age_seconds": int(age_seconds),
        }
    return {
        "target_date": str(target_date),
        "ttl_seconds": int(MEAL_CACHE_TTL.total_seconds()),
        "restaurants": restaurants,
    }


def _normalize_datetime(value: datetime, now: datetime) -> datetime:
    if value.tzinfo is None and now.tzinfo is not None:
        local_assumed = value.replace(tzinfo=now.tzinfo)
        utc_assumed = value.replace(tzinfo=timezone.utc).astimezone(now.tzinfo)
        local_age = abs((now - local_assumed).total_seconds())
        utc_age = abs((now - utc_assumed).total_seconds())
        if utc_age < local_age:
            return utc_assumed
        return local_assumed
    return value
