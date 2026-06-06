from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.kakao import KakaoRequest
from app.services.kakao_callback import find_callback_url, send_kakao_callback_response
from app.services.kakao_sync_response import create_fast_sync_response
from app.wait_messages import random_wait_message


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/kakao/callback")
def kakao_callback(payload: KakaoRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user_request = payload.userRequest or {}
    user_info = user_request.get("user") or {}
    kakao_user_id = str(user_info.get("id") or user_info.get("properties", {}).get("plusfriendUserKey") or "anonymous")
    utterance = str(user_request.get("utterance") or "").strip()
    raw_payload = payload.model_dump()
    callback_url = find_callback_url(raw_payload)
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
            raw_payload,
        )
        return {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": random_wait_message(),
                "utterance": utterance,
            },
        }

    return create_fast_sync_response(db, kakao_user_id, utterance, raw_payload)
