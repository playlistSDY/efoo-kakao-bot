from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repositories as repo
from app.chatbot import load_meal_context, meal_chat_agent
from app.config import settings
from app.database import get_db, init_db
from app.kakao_templates import build_kakao_response
from app.scheduler import shutdown_scheduler, start_scheduler


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
def kakao_callback(payload: KakaoRequest, db: Session = Depends(get_db)):
    user_request = payload.userRequest or {}
    user_info = user_request.get("user") or {}
    kakao_user_id = str(user_info.get("id") or user_info.get("properties", {}).get("plusfriendUserKey") or "anonymous")
    utterance = str(user_request.get("utterance") or "").strip()

    user = repo.get_or_create_user(db, kakao_user_id)
    session = repo.get_or_create_active_session(db, user)
    repo.add_message(db, session.id, "user", utterance, payload.model_dump())

    answer = meal_chat_agent.run(db, user, session, utterance)
    repo.add_message(db, session.id, "assistant", answer)
    _, _, meals = load_meal_context(db, utterance, datetime.now(ZoneInfo(settings.APP_TIMEZONE)))

    return build_kakao_response(answer, meals)
