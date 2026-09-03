from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.config import settings
from app.domain.restaurants import DEFAULT_RESTAURANT_INFO


WEEKDAY_TO_INDEX = {
    "월요일": 0,
    "월욜": 0,
    "화요일": 1,
    "화욜": 1,
    "수요일": 2,
    "수욜": 2,
    "목요일": 3,
    "목욜": 3,
    "금요일": 4,
    "금욜": 4,
    "토요일": 5,
    "토욜": 5,
    "일요일": 6,
    "일욜": 6,
}


def infer_target_date(text: str, now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if "모레" in text:
        return (now + timedelta(days=2)).date()
    if "내일" in text:
        return (now + timedelta(days=1)).date()
    if "어제" in text:
        return (now - timedelta(days=1)).date()
    explicit_date = _infer_explicit_date(text, now)
    if explicit_date:
        return explicit_date
    weekday_date = _infer_weekday_date(text, now)
    if weekday_date:
        return weekday_date
    return now.date()


def infer_meal_types(text: str, now: datetime | None = None) -> list[str] | None:
    now = now or datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    if "아침" in text or "조식" in text:
        return ["조식"]
    if "점심" in text or "중식" in text:
        return ["중식"]
    if "저녁" in text or "석식" in text:
        return ["석식"]

    target_date = infer_target_date(text, now)
    if target_date != now.date():
        return None

    if now.hour < 10:
        return ["조식", "중식"]
    if now.hour < 16:
        return ["중식"]
    return ["석식"]


def infer_meal_intent(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if any(keyword in compact for keyword in ["뭐나와", "뭐나옴", "뭐나오", "머나와", "머나옴"]):
        return True
    if re.search(r"(오늘|내일|모레|월요일|화요일|수요일|목요일|금요일|토요일|일요일|월욜|화욜|수욜|목욜|금욜|토욜|일욜).*(뭐|머).*(나와|나옴|나오|있어)", normalized):
        return True
    if re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일.*(뭐|머).*(나와|나옴|나오|있어)", normalized):
        return True
    meal_keywords = [
        "학식",
        "교식",
        "긱식",
        "창보",
        "창의",
        "창의인재",
        "기숙사식당",
        "학생식당",
        "교직원식당",
        "창업보육",
        "식당",
        "메뉴",
        "밥",
        "먹",
        "먹을까",
        "조식",
        "아침",
        "중식",
        "점심",
        "석식",
        "저녁",
        "배고",
        "식사",
        "운영시간",
        "몇시",
        "몇 시",
        "열어",
        "닫아",
        "마감",
    ]
    return any(keyword in normalized for keyword in meal_keywords)


def infer_restaurant_codes(text: str) -> list[str] | None:
    compact = re.sub(r"\s+", "", text)
    if any(marker in compact for marker in ("식당별", "전체식당", "모든식당", "각식당")):
        return None

    matches = []
    for code, info in DEFAULT_RESTAURANT_INFO.items():
        names = [info["name"], *info.get("aliases", [])]
        if any(name and name in compact for name in names):
            matches.append(code)
    return matches or None


def is_fast_meal_lookup(text: str) -> bool:
    """Whether a query is safe to answer from deterministic meal lookup alone."""
    normalized = text.strip()
    if not infer_meal_intent(normalized):
        return False

    contextual_markers = (
        "그거",
        "그건",
        "그게",
        "그 메뉴",
        "그 식당",
        "거기",
        "방금",
        "아까",
        "그러면",
        "그럼",
        "전에",
        "지난 대화",
        "다른 곳",
        "다른 메뉴",
    )
    judgment_markers = (
        "추천",
        "골라",
        "비교",
        "뭐 먹",
        "뭘 먹",
        "먹을까",
        "맛있",
        "가성비",
        "저렴",
        "더 싼",
        "더 비싼",
        "알레르기",
        "취향",
        "선호",
    )
    restaurant_info_markers = ("운영시간", "몇시", "몇 시", "위치", "어디", "열어", "닫아", "마감")
    if any(marker in normalized for marker in contextual_markers + judgment_markers + restaurant_info_markers):
        return False
    if _mentions_multiple_dates(normalized):
        return False

    compact = re.sub(r"\s+", "", normalized)
    direct_markers = (
        "메뉴",
        "식단",
        "급식",
        "학식",
        "교식",
        "긱식",
        "창보",
        "학생식당",
        "교직원식당",
        "창의인재원식당",
        "기숙사식당",
        "창업보육센터",
        "조식",
        "중식",
        "석식",
        "아침",
        "점심",
        "저녁",
        "뭐나와",
        "뭐나옴",
        "뭐나오",
        "머나와",
    )
    return any(marker in compact for marker in direct_markers)


def format_target_date_text(target_date: date) -> str:
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return f"{target_date.year}-{target_date.month:02d}-{target_date.day:02d} {weekdays[target_date.weekday()]}"


def _infer_weekday_date(text: str, now: datetime) -> date | None:
    match = re.search(r"(지난|저번|이번|다음)?\s*(월요일|월욜|화요일|화욜|수요일|수욜|목요일|목욜|금요일|금욜|토요일|토욜|일요일|일욜)(?:날|에)?", text)
    if not match:
        return None

    modifier = match.group(1) or ""
    weekday_text = match.group(2)
    target_weekday = WEEKDAY_TO_INDEX[weekday_text]
    today_weekday = now.weekday()

    if modifier in {"지난", "저번"}:
        days_back = (today_weekday - target_weekday) % 7 or 7
        return (now - timedelta(days=days_back)).date()

    days_ahead = (target_weekday - today_weekday) % 7
    if modifier == "다음" and days_ahead == 0:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).date()


def _infer_explicit_date(text: str, now: datetime) -> date | None:
    month_day = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if month_day:
        month = int(month_day.group(1))
        day = int(month_day.group(2))
        try:
            return date(now.year, month, day)
        except ValueError:
            return None

    day_only = re.search(r"(?<!월\s)(\d{1,2})\s*일", text)
    if day_only:
        day = int(day_only.group(1))
        try:
            return date(now.year, now.month, day)
        except ValueError:
            return None

    return None


def _mentions_multiple_dates(text: str) -> bool:
    relative_dates = {marker for marker in ("오늘", "내일", "모레", "어제") if marker in text}
    explicit_dates = re.findall(r"\d{1,2}\s*월\s*\d{1,2}\s*일", text)
    weekdays = {
        match.group(0)
        for match in re.finditer(r"월요일|화요일|수요일|목요일|금요일|토요일|일요일|월욜|화욜|수욜|목욜|금욜|토욜|일욜", text)
    }
    return len(relative_dates) + len(explicit_dates) + len(weekdays) > 1
