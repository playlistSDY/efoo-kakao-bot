from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.domain.recommendation import RecommendationIntent
from app.services.prompt_loader import load_prompt_template


logger = logging.getLogger(__name__)


EMPTY_RECOMMENDATION_INTENT = RecommendationIntent(
    desired_foods=[],
    desired_cuisines=[],
    desired_traits=[],
    avoid_foods=[],
    avoid_traits=[],
    matching_keywords=[],
    budget_limit=None,
)


def extract_recommendation_intent(user_text: str) -> RecommendationIntent:
    if not settings.OPENAI_API_KEY:
        return EMPTY_RECOMMENDATION_INTENT

    prompt = load_prompt_template("recommendation_intent.txt").format(user_text=user_text)
    llm = ChatOpenAI(api_key=lambda: settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL, temperature=0)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
        return _parse_recommendation_intent(content)
    except Exception:
        logger.exception("추천 조건 추출 실패: user_text=%s", user_text)
        return EMPTY_RECOMMENDATION_INTENT


def format_recommendation_intent(intent: RecommendationIntent) -> str:
    if intent.is_empty():
        return "추출된 추천 조건 없음"
    lines = []
    if intent.desired_foods:
        lines.append(f"원하는 음식: {', '.join(intent.desired_foods)}")
    if intent.desired_cuisines:
        lines.append(f"원하는 음식 종류: {', '.join(intent.desired_cuisines)}")
    if intent.desired_traits:
        lines.append(f"원하는 특성: {', '.join(intent.desired_traits)}")
    if intent.avoid_foods:
        lines.append(f"피할 음식: {', '.join(intent.avoid_foods)}")
    if intent.avoid_traits:
        lines.append(f"피할 특성: {', '.join(intent.avoid_traits)}")
    if intent.matching_keywords:
        lines.append(f"점수 매칭 키워드: {', '.join(intent.matching_keywords)}")
    if intent.budget_limit:
        lines.append(f"예산 상한: {intent.budget_limit}원")
    return "\n".join(lines)


def _parse_recommendation_intent(content: str) -> RecommendationIntent:
    payload = _extract_json_object(content)
    return RecommendationIntent(
        desired_foods=_string_list(payload.get("desired_foods")),
        desired_cuisines=_string_list(payload.get("desired_cuisines")),
        desired_traits=_string_list(payload.get("desired_traits")),
        avoid_foods=_string_list(payload.get("avoid_foods")),
        avoid_traits=_string_list(payload.get("avoid_traits")),
        matching_keywords=_string_list(payload.get("matching_keywords")),
        budget_limit=_int_or_none(payload.get("budget_limit")),
    )


def _extract_json_object(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?", "", normalized).strip()
        normalized = re.sub(r"```$", "", normalized).strip()

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", normalized, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    return parsed if isinstance(parsed, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return items[:12]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        digits = re.sub(r"[^0-9]", "", value)
        return int(digits) if digits else None
    return None
