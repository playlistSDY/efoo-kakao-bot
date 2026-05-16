from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.config import settings


@tool
def get_current_datetime() -> str:
    """현재 날짜, 요일, 시간을 Asia/Seoul 기준으로 반환한다."""
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return now.strftime(f"%Y-%m-%d ({weekdays[now.weekday()]}) %H:%M:%S {settings.APP_TIMEZONE}")


CHAT_TOOLS = [get_current_datetime]
