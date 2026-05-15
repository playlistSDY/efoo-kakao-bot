from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from langchain_core.tools import tool

from app.config import settings


WEATHER_CODE_KO = {
    0: "맑음",
    1: "대체로 맑음",
    2: "부분적으로 흐림",
    3: "흐림",
    45: "안개",
    48: "서리 안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    95: "뇌우",
}


@tool
def get_current_datetime() -> str:
    """현재 날짜, 요일, 시간을 Asia/Seoul 기준으로 반환한다."""
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return now.strftime(f"%Y-%m-%d ({weekdays[now.weekday()]}) %H:%M:%S {settings.APP_TIMEZONE}")


@tool
def get_current_weather(location: str = "") -> str:
    """특정 지역의 현재 날씨를 조회한다. 지역이 비어 있으면 기본 캠퍼스 지역을 사용한다."""
    query = (location or settings.DEFAULT_WEATHER_LOCATION).strip()
    geo = _geocode(query)
    if not geo:
        return f"{query} 위치를 찾지 못했습니다."

    params = {
        "latitude": geo["latitude"],
        "longitude": geo["longitude"],
        "current": "temperature_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m",
        "timezone": settings.APP_TIMEZONE,
    }
    response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=5)
    response.raise_for_status()
    payload = response.json()
    current = payload.get("current", {})
    code = current.get("weather_code")
    weather = WEATHER_CODE_KO.get(code, f"날씨코드 {code}")
    place = ", ".join(part for part in [geo.get("name"), geo.get("admin1"), geo.get("country")] if part)
    return (
        f"{place} 현재 날씨: {weather}, "
        f"기온 {current.get('temperature_2m')}°C, "
        f"체감 {current.get('apparent_temperature')}°C, "
        f"강수량 {current.get('precipitation')}mm, "
        f"비 {current.get('rain')}mm, "
        f"풍속 {current.get('wind_speed_10m')}km/h"
    )


def _geocode(location: str) -> dict | None:
    params = {"name": location, "count": 1, "language": "ko", "format": "json"}
    response = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=params, timeout=5)
    response.raise_for_status()
    results = response.json().get("results") or []
    return results[0] if results else None


CHAT_TOOLS = [get_current_datetime, get_current_weather]
