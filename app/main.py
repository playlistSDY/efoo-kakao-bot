from contextlib import asynccontextmanager
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

import requests
from fastapi import BackgroundTasks, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repositories as repo
from app.chatbot import infer_target_date, meal_chat_agent
from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.kakao_templates import build_kakao_response
from app.meal_cache import get_meal_cache_status
from app.response_policy import choose_kakao_presentation
from app.wait_messages import random_wait_message


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Efoo 학식 추천 카카오톡 챗봇", lifespan=lifespan)


class KakaoRequest(BaseModel):
    userRequest: dict = Field(default_factory=dict)
    action: dict | None = None
    bot: dict | None = None
    contexts: list[dict] | None = None


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/test/chat")
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

    return {
        "user_id": user_id,
        "message": message,
        "target_date": str(target_date),
        "lookup": debug_result["lookup"],
        "agent_steps": debug_result.get("agent_steps", []),
        "tool_calls": debug_result["tool_calls"],
        "cache": get_meal_cache_status(db, target_date, now),
        "presentation": presentation.__dict__,
        "answer": answer,
        "kakao_response": build_kakao_response(answer, meals if presentation.attach_meal_cards else []),
    }


@app.post("/kakao/callback")
def kakao_callback(payload: KakaoRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user_request = payload.userRequest or {}
    user_info = user_request.get("user") or {}
    kakao_user_id = str(user_info.get("id") or user_info.get("properties", {}).get("plusfriendUserKey") or "anonymous")
    utterance = str(user_request.get("utterance") or "").strip()
    callback_url = find_callback_url(payload.model_dump())
    logger.info(
        "카카오 스킬 요청 수신: user=%s callback=%s utterance=%s",
        kakao_user_id,
        bool(callback_url),
        utterance,
    )

    if callback_url:
        background_tasks.add_task(
            send_kakao_callback_response,
            callback_url,
            kakao_user_id,
            utterance,
            payload.model_dump(),
        )
        return {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": random_wait_message(),
                "utterance": utterance,
            },
        }

    return create_chat_response(db, kakao_user_id, utterance, payload.model_dump())


@app.get("/test/callback")
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

    return build_kakao_response(answer, meals if presentation.attach_meal_cards else [])


def find_callback_url(payload: dict) -> str | None:
    user_request = payload.get("userRequest") or {}
    candidates = [
        user_request.get("callbackUrl"),
        user_request.get("callback_url"),
        payload.get("callbackUrl"),
        payload.get("callback_url"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def send_kakao_callback_response(callback_url: str, kakao_user_id: str, utterance: str, raw_payload: dict) -> None:
    db = SessionLocal()
    try:
        response_payload = create_chat_response(db, kakao_user_id, utterance, raw_payload)
        response = requests.post(callback_url, json=response_payload, timeout=10)
        response.raise_for_status()
        logger.info("카카오 callback 응답 전송 완료: status=%s", response.status_code)
    except Exception:
        logger.exception("카카오 callback 응답 전송 실패")
    finally:
        db.close()
