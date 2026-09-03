from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.domain.restaurants import DEFAULT_RESTAURANT_INFO


load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw in (None, "") else float(raw)


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
    OPENAI_TOOL_REASONING_EFFORT: str = os.getenv("OPENAI_TOOL_REASONING_EFFORT", "none")
    OPENAI_REASONING_EFFORT: str = os.getenv("OPENAI_REASONING_EFFORT", "low")
    OPENAI_MAX_TOOL_ROUNDS: int = _int_env("OPENAI_MAX_TOOL_ROUNDS", 4)
    OPENAI_TIMEOUT_SECONDS: float = _float_env("OPENAI_TIMEOUT_SECONDS", 15.0)
    AGENT_TIME_BUDGET_SECONDS: float = _float_env("AGENT_TIME_BUDGET_SECONDS", 40.0)
    MEAL_HTTP_CONNECT_TIMEOUT_SECONDS: float = _float_env("MEAL_HTTP_CONNECT_TIMEOUT_SECONDS", 2.0)
    MEAL_HTTP_READ_TIMEOUT_SECONDS: float = _float_env("MEAL_HTTP_READ_TIMEOUT_SECONDS", 5.0)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    MEAL_IMAGE_CACHE_DIR: str = os.getenv("MEAL_IMAGE_CACHE_DIR", "/data/meal-images")
    MEAL_IMAGE_MAX_BYTES: int = _int_env("MEAL_IMAGE_MAX_BYTES", 10 * 1024 * 1024)
    MEAL_IMAGE_REFRESH_MINUTES: int = _int_env("MEAL_IMAGE_REFRESH_MINUTES", 10)
    APP_TIMEZONE: str = os.getenv("APP_TIMEZONE", "Asia/Seoul")
    HANYANG_BASE_URL: str = os.getenv("HANYANG_BASE_URL", "https://www.hanyang.ac.kr")
    MEAL_FETCH_DAYS_AHEAD: int = _int_env("MEAL_FETCH_DAYS_AHEAD", 7)
    RESTAURANT_CODES: dict[str, str] = field(default_factory=_restaurants_env)


settings = Settings()
