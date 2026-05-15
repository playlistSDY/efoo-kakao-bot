from contextlib import asynccontextmanager
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

import requests
from fastapi import BackgroundTasks, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repositories as repo
from app.chatbot import load_meal_context, meal_chat_agent
from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.kakao_templates import build_kakao_response
from app.scheduler import shutdown_scheduler, start_scheduler
from app.wait_messages import random_wait_message


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


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
    user = repo.get_or_create_user(db, user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", message, {"source": "test-chat"})

    answer = meal_chat_agent.run(db, user, session, message)
    repo.add_message(db, session.id, "assistant", answer)
    _, _, meals = load_meal_context(db, message, datetime.now(ZoneInfo(settings.APP_TIMEZONE)))

    return {
        "user_id": user_id,
        "message": message,
        "answer": answer,
        "kakao_response": build_kakao_response(answer, meals),
    }


@app.post("/kakao/callback")
def kakao_callback(payload: KakaoRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user_request = payload.userRequest or {}
    user_info = user_request.get("user") or {}
    kakao_user_id = str(user_info.get("id") or user_info.get("properties", {}).get("plusfriendUserKey") or "anonymous")
    utterance = str(user_request.get("utterance") or "").strip()
    callback_url = user_request.get("callbackUrl")

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


def create_chat_response(db: Session, kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE))

    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, raw_payload)

    answer = meal_chat_agent.run(db, user, session, utterance)
    repo.add_message(db, session.id, "assistant", answer)
    _, _, meals = load_meal_context(db, utterance, now)

    return build_kakao_response(answer, meals)


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
