from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.database import SessionLocal
from app.domain.meal_intent import infer_target_date
from app.services.chatbot import meal_chat_agent
from app.services.kakao_templates import build_kakao_response
from app.services.quick_replies import build_quick_replies
from app.services.response_policy import choose_kakao_presentation


def create_chat_response(db: Session, kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))

    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, raw_payload)

    debug_result = meal_chat_agent.run_debug(db, user, session, utterance)
    answer = debug_result["answer"]
    repo.add_message(db, session.id, "assistant", answer)
    target_date = infer_target_date(utterance, now)
    meals = debug_result.get("meals", [])
    presentation = choose_kakao_presentation(utterance, target_date, meals)
    quick_replies = build_quick_replies(
        utterance,
        target_date,
        meals,
        bool(debug_result["lookup"].get("meal_intent")),
        now,
        answer,
    )

    return build_kakao_response(answer, meals if presentation.attach_meal_cards else [], quick_replies)


def create_chat_response_in_new_session(kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    db = SessionLocal()
    try:
        return create_chat_response(db, kakao_user_id, utterance, raw_payload)
    finally:
        db.close()
