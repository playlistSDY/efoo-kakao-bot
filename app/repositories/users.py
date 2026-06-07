from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserProfile


def get_or_create_user(db: Session, kakao_user_id: str) -> UserProfile:
    user = db.scalar(select(UserProfile).where(UserProfile.kakao_user_id == kakao_user_id))
    if user:
        return user
    user = UserProfile(kakao_user_id=kakao_user_id, allergies=[], preferences=[], dislikes=[])
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_profile_from_text(db: Session, user: UserProfile, text: str) -> UserProfile:
    allergies = _extract_after_keywords(text, ["알러지", "알레르기", "못 먹어"])
    preferences = _extract_after_keywords(text, ["좋아해", "선호", "취향"])
    dislikes = _extract_after_keywords(text, ["싫어", "비선호"])
    budget = _extract_budget(text)

    if allergies:
        user.allergies = _merge_list(user.allergies or [], allergies)
    if preferences:
        user.preferences = _merge_list(user.preferences or [], preferences)
    if dislikes:
        user.dislikes = _merge_list(user.dislikes or [], dislikes)
    if budget:
        user.budget_limit = budget

    db.commit()
    db.refresh(user)
    return user


def _extract_after_keywords(text: str, keywords: list[str]) -> list[str]:
    for keyword in keywords:
        if keyword not in text:
            continue
        head, tail = text.split(keyword, 1)
        candidates = []
        candidates.extend(_keyword_items_from_text(tail))
        candidates.extend(reversed(_keyword_items_from_text(head)))
        return _dedupe_keywords(candidates)[:6]
    return []


def _keyword_items_from_text(text: str) -> list[str]:
    text = re.sub(r"(있어|있음|야|입니다|이에요|예요|해|해요|함|이야|추천해줘|추천|메뉴|점심|저녁|아침|오늘|내일)", " ", text)
    items = []
    for item in re.split(r"[,/와과랑및 ]+", text):
        item = item.strip(" ,./")
        if len(item) < 2:
            continue
        if re.search(r"\d", item):
            continue
        if item in {"학식", "식당", "메뉴", "추천", "점심", "저녁", "아침", "오늘", "내일"}:
            continue
        items.append(item)
    return items


def _dedupe_keywords(items: list[str]) -> list[str]:
    deduped = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _extract_budget(text: str) -> int | None:
    match = re.search(r"(\d{1,6})\s*(천원|만원|원)", text)
    if not match:
        return None
    amount = int(match.group(1))
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
