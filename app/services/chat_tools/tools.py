from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.domain.restaurants import DEFAULT_RESTAURANT_INFO, meal_type_status
from app.models import Meal
from app.services.meals.cache import ensure_fresh_meals


MEAL_TYPES = {"조식", "중식", "석식"}

CHAT_TOOLS = [
    {
        "type": "function",
        "name": "get_current_datetime",
        "description": "대한민국 시간대의 현재 날짜, 요일, 시각을 정확히 조회한다.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_meals",
        "description": (
            "정확히 한 날짜의 한양대학교 ERICA 학식을 조회한다. 날짜, 식당, 조식/중식/석식을 "
            "좁혀 조회할 수 있으며 다른 날짜가 필요하면 이 도구를 다시 호출한다. 메뉴 질문에는 추측하지 말고 반드시 사용한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "조회 날짜. YYYY-MM-DD 형식"},
                "restaurant_codes": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "enum": sorted(DEFAULT_RESTAURANT_INFO)},
                    "description": "식당 코드 목록. 전체 식당이면 null",
                },
                "meal_types": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "enum": ["조식", "중식", "석식"]},
                    "description": "식사 종류 목록. 하루 전체면 null",
                },
                "refresh": {
                    "type": "boolean",
                    "description": "최신 정보가 필요하면 true. 일반 사용자 조회는 true",
                },
            },
            "required": ["date", "restaurant_codes", "meal_types", "refresh"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_restaurant_info",
        "description": "교내 식당의 이름, 줄임말, 위치, 운영시간과 오늘 현재 운영 상태를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_codes": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "enum": sorted(DEFAULT_RESTAURANT_INFO)},
                    "description": "식당 코드 목록. 전체 식당이면 null",
                }
            },
            "required": ["restaurant_codes"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class ChatToolExecutor:
    db: Session
    now: datetime = field(default_factory=lambda: datetime.now(ZoneInfo(settings.APP_TIMEZONE)))
    seen_meals: dict[int, Meal] = field(default_factory=dict)

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "get_current_datetime":
                result = self._get_current_datetime()
            elif name == "get_meals":
                result = self._get_meals(arguments)
            elif name == "get_restaurant_info":
                result = self._get_restaurant_info(arguments)
            else:
                result = {"ok": False, "error": f"알 수 없는 도구: {name}"}
        except (TypeError, ValueError) as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception:
            result = {"ok": False, "error": "도구 실행 중 일시적인 오류가 발생했습니다."}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)

    def selected_meals(self, meal_ids: list[int]) -> list[Meal]:
        selected = []
        for meal_id in meal_ids:
            meal = self.seen_meals.get(meal_id)
            if meal and meal not in selected:
                selected.append(meal)
        return selected

    def _get_current_datetime(self) -> dict[str, Any]:
        weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        return {
            "ok": True,
            "datetime": self.now.isoformat(),
            "date": self.now.date().isoformat(),
            "weekday": weekdays[self.now.weekday()],
            "timezone": settings.APP_TIMEZONE,
        }

    def _get_meals(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            target_date = date.fromisoformat(str(arguments.get("date", "")))
        except ValueError as exc:
            raise ValueError("date는 YYYY-MM-DD 형식이어야 합니다.") from exc

        restaurant_codes = _validated_restaurant_codes(arguments.get("restaurant_codes"))
        meal_types = _validated_meal_types(arguments.get("meal_types"))
        cache = None
        if arguments.get("refresh", True):
            cache = ensure_fresh_meals(self.db, target_date, self.now, restaurant_codes)
        meals = repo.get_meals_flexible(
            self.db,
            target_date=target_date,
            restaurant_codes=restaurant_codes,
            meal_types=meal_types,
        )
        for meal in meals:
            self.seen_meals[meal.id] = meal
        return {
            "ok": True,
            "date": target_date.isoformat(),
            "filters": {"restaurant_codes": restaurant_codes, "meal_types": meal_types},
            "cache": cache,
            "count": len(meals),
            "meals": [self._serialize_meal(meal) for meal in meals],
        }

    def _get_restaurant_info(self, arguments: dict[str, Any]) -> dict[str, Any]:
        codes = _validated_restaurant_codes(arguments.get("restaurant_codes")) or list(DEFAULT_RESTAURANT_INFO)
        restaurants = []
        for code in codes:
            info = DEFAULT_RESTAURANT_INFO[code]
            restaurants.append(
                {
                    "code": code,
                    **info,
                    "today_status": {
                        meal_type: meal_type_status(code, meal_type, self.now)
                        for meal_type in info.get("open_times", {})
                    },
                }
            )
        return {"ok": True, "restaurants": restaurants}

    def _serialize_meal(self, meal: Meal) -> dict[str, Any]:
        restaurant_code = meal.restaurant.code if meal.restaurant else None
        payload = {
            "meal_id": meal.id,
            "date": meal.date.isoformat(),
            "restaurant_code": restaurant_code,
            "restaurant_name": meal.restaurant.name if meal.restaurant else "식당",
            "meal_type": meal.meal_type,
            "menu": meal.korean_name or [],
            "tags": meal.tags or [],
            "price": meal.price,
            "image_url": meal.image_url,
        }
        if meal.date == self.now.date() and restaurant_code:
            payload["current_status"] = meal_type_status(restaurant_code, meal.meal_type, self.now)
        return payload


def _validated_restaurant_codes(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("restaurant_codes는 배열 또는 null이어야 합니다.")
    invalid = [str(code) for code in value if code not in DEFAULT_RESTAURANT_INFO]
    if invalid:
        raise ValueError(f"지원하지 않는 식당 코드: {', '.join(invalid)}")
    return list(dict.fromkeys(value)) or None


def _validated_meal_types(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("meal_types는 배열 또는 null이어야 합니다.")
    invalid = [str(meal_type) for meal_type in value if meal_type not in MEAL_TYPES]
    if invalid:
        raise ValueError(f"지원하지 않는 식사 종류: {', '.join(invalid)}")
    return list(dict.fromkeys(value)) or None
