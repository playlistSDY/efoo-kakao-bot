from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import repositories as repo
from app.config import settings
from app.database import get_db
from app.domain.meal_intent import infer_target_date
from app.kakao_templates import build_kakao_response
from app.meal_cache import get_meal_cache_status
from app.quick_replies import build_quick_replies
from app.response_policy import choose_kakao_presentation
from app.services.chatbot import meal_chat_agent
from app.services.kakao_callback import build_callback_timeout_response
from app.wait_messages import random_wait_message


router = APIRouter()


@router.get("/test/chat")
def test_chat(message: str = "오늘 점심 추천해줘", user_id: str = "test-user", db: Session = Depends(get_db)):
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
    user = repo.get_or_create_user(db, user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", message, {"source": "test-chat"})

    debug_result = meal_chat_agent.run_debug(db, user, session, message)
    answer = debug_result["answer"]
    repo.add_message(db, session.id, "assistant", answer)
    target_date = infer_target_date(message, now)
    meals = debug_result.get("meals", [])
    presentation = choose_kakao_presentation(message, target_date, meals)
    quick_replies = build_quick_replies(
        message,
        target_date,
        meals,
        bool(debug_result["lookup"].get("meal_intent")),
        now,
        answer,
    )

    return {
        "user_id": user_id,
        "message": message,
        "target_date": str(target_date),
        "lookup": debug_result["lookup"],
        "agent_steps": debug_result.get("agent_steps", []),
        "tool_calls": debug_result["tool_calls"],
        "cache": get_meal_cache_status(db, target_date, now),
        "presentation": presentation.__dict__,
        "quick_replies": quick_replies,
        "answer": answer,
        "kakao_response": build_kakao_response(answer, meals if presentation.attach_meal_cards else [], quick_replies),
    }


@router.get("/test/callback")
def test_callback_shape(message: str = "오늘 점심 추천해줘"):
    payload = {
        "userRequest": {
            "utterance": message,
            "callbackUrl": "https://example.com/kakao-callback-url",
            "user": {"id": "callback-shape-test"},
        }
    }
    return {
        "request_example": payload,
        "initial_response": {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": random_wait_message(),
                "utterance": message,
            },
        },
    }


@router.get("/test/callback-timeout")
def test_callback_timeout_shape():
    return build_callback_timeout_response()
