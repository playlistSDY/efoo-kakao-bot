from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import repositories as repo
from app.db import get_db
from app.services.chatbot import meal_chat_agent
from app.services.kakao_callback import build_callback_timeout_response
from app.services.kakao_templates import build_kakao_response
from app.services.wait_messages import random_wait_message


router = APIRouter()


@router.get("/test/chat")
def test_chat(message: str = "오늘 점심 추천해줘", user_id: str = "test-user", db: Session = Depends(get_db)):
    user = repo.get_or_create_user(db, user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", message, {"source": "test-chat"})

    result = meal_chat_agent.run_result(db, user, session, message)
    repo.add_message(db, session.id, "assistant", result.answer)

    return {
        "user_id": user_id,
        "message": message,
        "agent_steps": result.agent_steps,
        "tool_calls": result.tool_calls,
        "presentation": result.presentation,
        "quick_replies": result.quick_replies,
        "answer": result.answer,
        "kakao_response": build_kakao_response(
            result.answer,
            result.meals,
            result.quick_replies,
            presentation=result.presentation,
        ),
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
