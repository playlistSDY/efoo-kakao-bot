from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime
import logging
import random
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
from app.quick_replies import build_quick_replies
from app.response_policy import choose_kakao_presentation
from app.wait_messages import random_wait_message


logger = logging.getLogger(__name__)
CALLBACK_TIMEOUT_SECONDS = 50
CALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=4)

CALLBACK_TIMEOUT_MESSAGES = [
    "에푸가 생각을 너무 깊게 하다가 잠깐 기절했어요.\n아래 버튼으로 다시 불러주세요.",
    "에푸 머릿속에서 메뉴 회의가 너무 길어졌어요.\n이번 답변은 잠깐 멈췄어요.",
    "에푸가 열심히 찾다가 시간이 다 됐어요.\n원하는 식당 메뉴를 바로 눌러주세요.",
    "에푸가 메뉴판을 너무 오래 들여다봤어요.\n다시 누르면 바로 이어서 볼게요.",
    "에푸가 답을 정리하다가 1분을 넘길 뻔했어요.\n아래 메뉴로 빠르게 다시 볼 수 있어요.",
]

CALLBACK_TIMEOUT_QUICK_REPLIES = [
    {"label": "학식", "action": "message", "messageText": "학생식당 메뉴 알려줘"},
    {"label": "교식", "action": "message", "messageText": "교직원식당 메뉴 알려줘"},
    {"label": "창의", "action": "message", "messageText": "창의인재원식당 메뉴 알려줘"},
    {"label": "창보", "action": "message", "messageText": "창업보육센터 메뉴 알려줘"},
    {"label": "점심", "action": "message", "messageText": "오늘 점심 메뉴 알려줘"},
]


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


@app.get("/test/callback-timeout")
def test_callback_timeout_shape():
    return build_callback_timeout_response()


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
    future = CALLBACK_EXECUTOR.submit(create_chat_response_in_new_session, kakao_user_id, utterance, raw_payload)
    try:
        response_payload = future.result(timeout=CALLBACK_TIMEOUT_SECONDS)
        post_kakao_callback(callback_url, response_payload)
        logger.info("카카오 callback 응답 전송 완료")
    except TimeoutError:
        logger.warning("카카오 callback 응답 시간 초과, fallback 전송: user=%s utterance=%s", kakao_user_id, utterance)
        try:
            post_kakao_callback(callback_url, build_callback_timeout_response())
        except Exception:
            logger.exception("카카오 callback timeout fallback 전송 실패")
    except Exception:
        logger.exception("카카오 callback 응답 생성 실패, fallback 전송")
        try:
            post_kakao_callback(callback_url, build_callback_timeout_response())
        except Exception:
            logger.exception("카카오 callback fallback 전송 실패")


def create_chat_response_in_new_session(kakao_user_id: str, utterance: str, raw_payload: dict) -> dict:
    db = SessionLocal()
    try:
        return create_chat_response(db, kakao_user_id, utterance, raw_payload)
    finally:
        db.close()


def build_callback_timeout_response() -> dict:
    return build_kakao_response(
        random.choice(CALLBACK_TIMEOUT_MESSAGES),
        [],
        CALLBACK_TIMEOUT_QUICK_REPLIES,
    )


def post_kakao_callback(callback_url: str, payload: dict) -> None:
    response = requests.post(callback_url, json=payload, timeout=10)
    if response.status_code >= 400:
        logger.error(
            "카카오 callback POST 실패: status=%s body=%s payload=%s",
            response.status_code,
            response.text[:1000],
            str(payload)[:2000],
        )
    response.raise_for_status()
    logger.info("카카오 callback POST 완료: status=%s", response.status_code)
