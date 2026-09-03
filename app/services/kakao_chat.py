from __future__ import annotations

import logging
from time import monotonic

from sqlalchemy.orm import Session

from app import repositories as repo
from app.db import SessionLocal
from app.services.chatbot import meal_chat_agent
from app.services.kakao_templates import build_kakao_response


logger = logging.getLogger(__name__)


def create_chat_response(db: Session, kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    started_at = monotonic()
    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, raw_payload)

    result = meal_chat_agent.run_result(db, user, session, utterance)
    repo.add_message(
        db,
        session.id,
        "assistant",
        result.answer,
        {
            "presentation": result.presentation,
            "context_mode": result.context_mode,
            "tool_calls": result.tool_calls,
        },
    )
    response = build_kakao_response(
        result.answer,
        result.meals,
        result.quick_replies,
        presentation=result.presentation,
    )
    logger.info(
        "챗봇 응답 생성 완료: user=%s elapsed_ms=%.1f path=%s meals=%s presentation=%s",
        kakao_user_id,
        (monotonic() - started_at) * 1000,
        "fast" if "fast_meal_lookup:get_meals" in result.agent_steps else "agent",
        len(result.meals),
        result.presentation,
    )
    return response


def create_chat_response_in_new_session(kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    db = SessionLocal()
    try:
        return create_chat_response(db, kakao_user_id, utterance, raw_payload)
    finally:
        db.close()
