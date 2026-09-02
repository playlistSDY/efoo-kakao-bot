from __future__ import annotations

from sqlalchemy.orm import Session

from app import repositories as repo
from app.db import SessionLocal
from app.services.chatbot import meal_chat_agent
from app.services.kakao_templates import build_kakao_response


def create_chat_response(db: Session, kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, raw_payload)

    result = meal_chat_agent.run_result(db, user, session, utterance)
    repo.add_message(
        db,
        session.id,
        "assistant",
        result.answer,
        {"presentation": result.presentation, "tool_calls": result.tool_calls},
    )
    return build_kakao_response(
        result.answer,
        result.meals,
        result.quick_replies,
        presentation=result.presentation,
    )


def create_chat_response_in_new_session(kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    db = SessionLocal()
    try:
        return create_chat_response(db, kakao_user_id, utterance, raw_payload)
    finally:
        db.close()
