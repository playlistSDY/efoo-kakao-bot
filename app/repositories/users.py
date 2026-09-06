from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserProfile


def get_or_create_user(db: Session, kakao_user_id: str) -> UserProfile:
    user = db.scalar(select(UserProfile).where(UserProfile.kakao_user_id == kakao_user_id))
    if user:
        return user
    user = UserProfile(
        kakao_user_id=kakao_user_id,
        allergies=[],
        preferences=[],
        dislikes=[],
        conversation_preferences=[],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def save_user_memory(
    db: Session,
    user: UserProfile,
    category: str,
    action: str,
    value: str | None,
) -> UserProfile:
    list_fields = {
        "allergy": "allergies",
        "preference": "preferences",
        "dislike": "dislikes",
    }
    if category in list_fields:
        field_name = list_fields[category]
        current = list(getattr(user, field_name) or [])
        cleaned = _clean_memory_value(value)
        if action == "add" and cleaned:
            setattr(user, field_name, _merge_list(current, [cleaned]))
        elif action == "remove" and cleaned:
            setattr(user, field_name, [item for item in current if item != cleaned])
        elif action == "clear":
            setattr(user, field_name, [])
        else:
            raise ValueError("목록 기억에는 add, remove, clear 작업을 사용할 수 있습니다.")
    elif category == "budget":
        if action == "clear":
            user.budget_limit = None
        elif action == "set":
            budget = _extract_budget(value or "")
            if budget is None:
                raise ValueError("예산은 '7000원'처럼 금액으로 저장해야 합니다.")
            user.budget_limit = budget
        else:
            raise ValueError("예산 기억에는 set 또는 clear 작업을 사용할 수 있습니다.")
    elif category == "note":
        notes = [line for line in (user.extra_notes or "").splitlines() if line.strip()]
        cleaned = _clean_memory_value(value)
        if action == "add" and cleaned:
            notes = _merge_list(notes, [cleaned])
        elif action == "remove" and cleaned:
            notes = [note for note in notes if note != cleaned]
        elif action == "clear":
            notes = []
        else:
            raise ValueError("메모 기억에는 add, remove, clear 작업을 사용할 수 있습니다.")
        user.extra_notes = "\n".join(notes) or None
    elif category == "nickname":
        if action == "clear":
            user.nickname = None
        elif action == "set":
            nickname = _clean_memory_value(value)[:40]
            if not nickname:
                raise ValueError("호칭으로 저장할 이름이 필요합니다.")
            user.nickname = nickname
        else:
            raise ValueError("호칭 기억에는 set 또는 clear 작업을 사용할 수 있습니다.")
    elif category == "speech_style":
        if action == "clear":
            user.speech_style = None
        elif action == "set" and value in {"casual", "polite"}:
            user.speech_style = value
        else:
            raise ValueError("말투는 casual 또는 polite로 설정하거나 clear할 수 있습니다.")
    elif category == "conversation_preference":
        values = list(user.conversation_preferences or [])
        cleaned = _clean_memory_value(value)
        if action == "add" and cleaned:
            user.conversation_preferences = _merge_list(values, [cleaned])
        elif action == "remove" and cleaned:
            user.conversation_preferences = [item for item in values if item != cleaned]
        elif action == "clear":
            user.conversation_preferences = []
        else:
            raise ValueError("대화 설정에는 add, remove, clear 작업을 사용할 수 있습니다.")
    else:
        raise ValueError(f"지원하지 않는 사용자 기억 분류: {category}")

    db.commit()
    db.refresh(user)
    return user


def _extract_budget(text: str) -> int | None:
    match = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(천원|만원|원)", text)
    if not match:
        return None
    amount = int(match.group(1).replace(",", ""))
    unit = match.group(2)
    if unit == "만원":
        return amount * 10000
    if unit == "천원":
        return amount * 1000
    return amount


def _merge_list(current: list[str], new_items: list[str]) -> list[str]:
    merged = list(current)
    for item in new_items:
        if item and item not in merged:
            merged.append(item)
    return merged[:20]


def _clean_memory_value(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" ,./")[:100]
