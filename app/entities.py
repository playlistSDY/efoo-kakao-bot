from datetime import date as DateType, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Restaurant(Base):
    __tablename__ = "cafeteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(String(200))
    building: Mapped[str | None] = mapped_column(String(50))
    floor: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[str | None] = mapped_column(String(20))
    longitude: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(500))
    open_times: Mapped[dict[str, str] | None] = mapped_column(JSON)

    meals: Mapped[list["Meal"]] = relationship(back_populates="restaurant")


class Meal(Base):
    __tablename__ = "cafeteria_meals"
    __table_args__ = (
        Index("idx_restaurant_date", "restaurant_id", "date"),
        Index("idx_restaurant_date_type", "restaurant_id", "date", "meal_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("cafeteria.id"), nullable=False)
    date: Mapped[DateType] = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str | None] = mapped_column(String(10))
    meal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    korean_name: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    price: Mapped[str | None] = mapped_column(String(20))
    image_url: Mapped[str | None] = mapped_column(String(500))

    restaurant: Mapped[Restaurant] = relationship(back_populates="meals")


class MealFetchLog(Base):
    __tablename__ = "meal_fetch_logs"
    __table_args__ = (Index("idx_meal_fetch_log_restaurant_date", "restaurant_id", "date", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("cafeteria.id"), nullable=False)
    date: Mapped[DateType] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    message: Mapped[str | None] = mapped_column(String(500))


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    kakao_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    allergies: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    dislikes: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget_limit: Mapped[int | None] = mapped_column(nullable=True)
    extra_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (Index("idx_chat_session_user_active", "user_id", "ended_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[UserProfile] = relationship(back_populates="sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("idx_chat_message_session_created", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


# Backward-compatible placeholders used by the legacy monthly fetch script.
Rating: Any = None
Keyword: Any = None
MealKeywordReview: Any = None
