from __future__ import annotations

import logging

import requests

from app.services.kakao_chat import create_chat_response_in_new_session
from app.services.kakao_templates import build_kakao_response


logger = logging.getLogger(__name__)
CALLBACK_TIMEOUT_QUICK_REPLIES = [
    {"label": "학식", "action": "message", "messageText": "학생식당 메뉴 알려줘"},
    {"label": "교식", "action": "message", "messageText": "교직원식당 메뉴 알려줘"},
    {"label": "창의", "action": "message", "messageText": "창의인재원식당 메뉴 알려줘"},
    {"label": "창보", "action": "message", "messageText": "창업보육센터 메뉴 알려줘"},
    {"label": "점심", "action": "message", "messageText": "오늘 점심 메뉴 알려줘"},
]


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
    try:
        response_payload = create_chat_response_in_new_session(kakao_user_id, utterance, raw_payload)
        post_kakao_callback(callback_url, response_payload)
        logger.info("카카오 callback 응답 전송 완료")
    except Exception:
        logger.exception("카카오 callback 응답 생성 실패: user=%s utterance=%s", kakao_user_id, utterance)
        try:
            post_kakao_callback(callback_url, build_callback_error_response())
        except Exception:
            logger.exception("카카오 callback fallback 전송 실패")


def build_callback_timeout_response() -> dict:
    return build_callback_error_response()


def build_callback_error_response() -> dict:
    return build_kakao_response(
        "메뉴 조회 중 일시적인 오류가 생겼어요.\n아래에서 식당을 골라 다시 확인해 주세요.",
        [],
        CALLBACK_TIMEOUT_QUICK_REPLIES,
    )


def post_kakao_callback(callback_url: str, payload: dict) -> None:
    response = requests.post(callback_url, json=payload, timeout=5)
    if response.status_code >= 400:
        logger.error(
            "카카오 callback POST 실패: status=%s body=%s payload=%s",
            response.status_code,
            response.text[:1000],
            str(payload)[:2000],
        )
    response.raise_for_status()
    logger.info("카카오 callback POST 완료: status=%s", response.status_code)
