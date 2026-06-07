from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db import SessionLocal
from app.services.meals.fetcher import meal_fetcher


logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone=settings.APP_TIMEZONE)


def fetch_meals_job() -> None:
    db = SessionLocal()
    try:
        meal_fetcher.fetch_and_store_meals(db)
    except Exception:
        logger.exception("예약 급식 정보 수집 실패")
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        fetch_meals_job,
        CronTrigger(hour=0, minute=0, timezone=settings.APP_TIMEZONE),
        id="daily_meal_fetch",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("급식 크롤링 스케줄러 시작: 매일 00:00 %s", settings.APP_TIMEZONE)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
