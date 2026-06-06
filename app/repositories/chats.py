from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import ChatMessage, ChatSession, UserProfile


def get_or_create_active_session(db: Session, user: UserProfile) -> ChatSession:
    session = db.scalar(
        select(ChatSession)
        .where(ChatSession.user_id == user.id, ChatSession.ended_at.is_(None))
        .order_by(ChatSession.started_at.desc())
    )
    if session:
        return session
    session = ChatSession(user_id=user.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_message(db: Session, session_id: int, role: str, content: str, raw_payload: dict | None = None) -> ChatMessage:
    message = ChatMessage(session_id=session_id, role=role, content=content, raw_payload=raw_payload)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_recent_messages(db: Session, session_id: int, limit: int = 12) -> list[ChatMessage]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))
