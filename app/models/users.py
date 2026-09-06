from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.chats import ChatSession


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    kakao_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    allergies: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    dislikes: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget_limit: Mapped[int | None] = mapped_column(nullable=True)
    extra_notes: Mapped[str | None] = mapped_column(Text)
    nickname: Mapped[str | None] = mapped_column(String(40), nullable=True)
    speech_style: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conversation_preferences: Mapped[list[str] | None] = mapped_column(JSON, default=list, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sessions: Mapped[list[ChatSession]] = relationship(back_populates="user")
