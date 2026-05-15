from datetime import datetime, time


DEFAULT_RESTAURANT_INFO = {
    "re11": {
        "name": "교직원식당",
        "aliases": ["교식"],
        "building": "복지관",
        "floor": "3층",
        "location": "복지관 3층",
        "open_times": {
            "중식": "11:30-13:30",
        },
    },
    "re12": {
        "name": "학생식당",
        "aliases": ["학식"],
        "building": "복지관",
        "floor": "2층",
        "location": "복지관 2층",
        "open_times": {
            "조식": "08:30-09:40",
            "중식": "11:30-13:30",
        },
    },
    "re13": {
        "name": "창의인재원식당",
        "aliases": ["창의", "창의인재", "긱식", "기숙사식당"],
        "building": "창의관",
        "floor": "1층",
        "location": "창의관 1층",
        "open_times": {
            "조식": "07:40-09:00",
            "중식": "11:30-13:20",
            "석식": "17:10-18:40",
        },
    },
    "re15": {
        "name": "창업보육센터",
        "aliases": ["창보"],
        "building": "창업보육센터",
        "floor": "지하 1층",
        "location": "창업보육센터 지하 1층",
        "open_times": {
            "중식": "11:30-13:30",
            "석식": "17:00-18:30",
        },
    },
}


def get_restaurant_info(code: str) -> dict:
    return DEFAULT_RESTAURANT_INFO.get(code, {})


def format_restaurant_context() -> str:
    lines = []
    for code, info in DEFAULT_RESTAURANT_INFO.items():
        aliases = ", ".join(info.get("aliases", [])) or "없음"
        open_times = ", ".join(f"{meal_type} {time}" for meal_type, time in info.get("open_times", {}).items())
        lines.append(f"- {info['name']}({code}, 줄임말: {aliases}): 위치 {info['location']}, 운영시간 {open_times}")
    return "\n".join(lines)


def format_open_status_context(now: datetime) -> str:
    lines = []
    for code, info in DEFAULT_RESTAURANT_INFO.items():
        statuses = []
        for meal_type, time_range in info.get("open_times", {}).items():
            start, end = _parse_time_range(time_range)
            if not start or not end:
                continue
            current = now.time()
            if start <= current <= end:
                state = "운영 중"
            elif current < start:
                state = f"운영 전, {time_range} 운영"
            else:
                state = f"운영 종료, {time_range} 운영"
            statuses.append(f"{meal_type}: {state}")
        lines.append(f"- {info['name']}({code}): {', '.join(statuses) if statuses else '운영시간 정보 없음'}")
    return "\n".join(lines)


def meal_type_status(restaurant_code: str, meal_type: str, now: datetime) -> str:
    info = get_restaurant_info(restaurant_code)
    time_range = (info.get("open_times") or {}).get(meal_type)
    if not time_range:
        return "운영시간 정보 없음"
    start, end = _parse_time_range(time_range)
    if not start or not end:
        return "운영시간 정보 없음"
    current = now.time()
    if start <= current <= end:
        return f"운영 중({time_range})"
    if current < start:
        return f"운영 전({time_range})"
    return f"운영 종료({time_range})"


def _parse_time_range(time_range: str) -> tuple[time | None, time | None]:
    try:
        start_text, end_text = [part.strip() for part in time_range.split("-", 1)]
        return time.fromisoformat(start_text), time.fromisoformat(end_text)
    except ValueError:
        return None, None
