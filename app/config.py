from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.domain.restaurants import DEFAULT_RESTAURANT_INFO


load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def _restaurants_env() -> dict[str, str]:
    raw = os.getenv("HANYANG_RESTAURANTS", "").strip()
    if not raw:
        return {code: info["name"] for code, info in DEFAULT_RESTAURANT_INFO.items()}

    restaurants = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        if ":" in item:
            code, name = item.split(":", 1)
        else:
            code, name = item, item
        restaurants[code.strip()] = name.strip()
    return restaurants or {code: info["name"] for code, info in DEFAULT_RESTAURANT_INFO.items()}


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./efoo_chatbot.db")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    OPENAI_REASONING_EFFORT: str = os.getenv("OPENAI_REASONING_EFFORT", "low")
    OPENAI_MAX_TOOL_ROUNDS: int = _int_env("OPENAI_MAX_TOOL_ROUNDS", 8)
    APP_TIMEZONE: str = os.getenv("APP_TIMEZONE", "Asia/Seoul")
    HANYANG_BASE_URL: str = os.getenv("HANYANG_BASE_URL", "https://www.hanyang.ac.kr")
    MEAL_FETCH_DAYS_AHEAD: int = _int_env("MEAL_FETCH_DAYS_AHEAD", 7)
    RESTAURANT_CODES: dict[str, str] = field(default_factory=_restaurants_env)


settings = Settings()
