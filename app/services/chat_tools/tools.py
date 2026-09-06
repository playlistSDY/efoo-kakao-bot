from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.domain.restaurants import (
    DEFAULT_RESTAURANT_INFO,
    meal_service_note,
    meal_service_time,
    meal_type_status,
)
from app.models import Meal, UserProfile
from app.services.meals.cache import ensure_fresh_meals
from app.services.meals.image_cache import public_meal_image_url


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
            "정확히 한 날짜의 한양대학교 ERICA 학식과 식당별 제공시간 안내를 조회한다. 날짜, 식당, "
            "조식/중식/석식을 좁혀 조회할 수 있으며 다른 날짜가 필요하면 이 도구를 다시 호출한다. "
            "메뉴 질문에는 추측하지 말고 반드시 사용한다."
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
    {
        "type": "function",
        "name": "recall_conversation",
        "description": (
            "현재 메시지가 이전 대화에 의존할 때만 사용자의 과거 대화를 불러온다. '그거', '아까', '전에', "
            "'다른 곳은?' 같은 후속 표현이나 과거 추천을 명시적으로 물을 때 사용한다. 독립적인 새 질문에는 사용하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "찾을 주제의 짧은 핵심어. 단순히 직전 대화가 필요하면 null",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 12,
                    "description": "반환할 최대 메시지 수",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "save_user_memory",
        "description": (
            "사용자가 명확히 밝힌 장기적인 식사 관련 정보만 프로필 DB에 저장·수정·삭제한다. "
            "알레르기, 반복되는 선호/비선호, 평소 예산, 식사 관련 메모에 사용한다. 오늘 한 번만의 요구는 저장하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["allergy", "preference", "dislike", "budget", "note"],
                },
                "action": {"type": "string", "enum": ["add", "remove", "set", "clear"]},
                "value": {
                    "type": ["string", "null"],
                    "description": (
                        "저장하거나 삭제할 짧은 값. 선호/비선호/알레르기는 '매운 음식', '오이', '땅콩'처럼 "
                        "분류 표현을 뺀 핵심 명사만 쓴다. 전체 삭제(clear)라면 null"
                    ),
                },
            },
            "required": ["category", "action", "value"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class ChatToolExecutor:
    db: Session
    now: datetime = field(default_factory=lambda: datetime.now(ZoneInfo(settings.APP_TIMEZONE)))
    user_id: int | None = None
    excluded_message_ids: set[int] = field(default_factory=set)
    seen_meals: dict[int, Meal] = field(default_factory=dict)

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "get_current_datetime":
                result = self._get_current_datetime()
            elif name == "get_meals":
                result = self._get_meals(arguments)
            elif name == "get_restaurant_info":
                result = self._get_restaurant_info(arguments)
            elif name == "recall_conversation":
                result = self._recall_conversation(arguments)
            elif name == "save_user_memory":
                result = self._save_user_memory(arguments)
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
            cache = ensure_fresh_meals(
                self.db,
                target_date,
                self.now,
                restaurant_codes,
                stale_while_revalidate=True,
                background_if_missing=bool(arguments.get("background_if_missing", False)),
            )
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
            "image_url": public_meal_image_url(meal.image_url),
            "service_time": meal_service_time(restaurant_code, meal.meal_type) if restaurant_code else None,
            "service_note": (
                meal_service_note(restaurant_code, meal.meal_type, meal.date, self.now)
                if restaurant_code
                else None
            ),
        }
        if meal.date == self.now.date() and restaurant_code:
            payload["current_status"] = meal_type_status(restaurant_code, meal.meal_type, self.now)
        return payload

    def _recall_conversation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.user_id is None:
            return {"ok": False, "error": "사용자 대화 범위를 확인할 수 없습니다."}
        limit = max(2, min(int(arguments.get("limit", 6)), 12))
        query = str(arguments.get("query") or "").strip()
        messages = repo.get_user_messages(
            self.db,
            self.user_id,
            limit=100,
            exclude_message_ids=self.excluded_message_ids,
        )
        selected = _select_relevant_messages(messages, query, limit)
        return {
            "ok": True,
            "query": query or None,
            "count": len(selected),
            "messages": [
                {
                    "message_id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
                for message in selected
            ],
        }

    def _save_user_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.user_id is None:
            return {"ok": False, "error": "사용자 프로필을 확인할 수 없습니다."}
        user = self.db.get(UserProfile, self.user_id)
        if user is None:
            return {"ok": False, "error": "사용자 프로필을 찾지 못했습니다."}
        user = repo.save_user_memory(
            self.db,
            user,
            str(arguments.get("category", "")),
            str(arguments.get("action", "")),
            arguments.get("value"),
        )
        return {
            "ok": True,
            "profile": {
                "allergies": user.allergies or [],
                "preferences": user.preferences or [],
                "dislikes": user.dislikes or [],
                "budget_limit": user.budget_limit,
                "notes": [line for line in (user.extra_notes or "").splitlines() if line.strip()],
            },
        }


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


def _select_relevant_messages(messages: list, query: str, limit: int) -> list:
    if not query:
        return messages[-limit:]
    keywords = [token for token in re.findall(r"[0-9A-Za-z가-힣]+", query.lower()) if len(token) >= 2]
    if not keywords:
        return messages[-limit:]
    scored = []
    for index, message in enumerate(messages):
        content = message.content.lower()
        score = sum(1 for keyword in keywords if keyword in content)
        if score:
            scored.append((score, index, message))
    if not scored:
        return messages[-limit:]
    selected_ids = {
        message.id
        for _, _, message in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:limit]
    }
    return [message for message in messages if message.id in selected_ids][-limit:]
