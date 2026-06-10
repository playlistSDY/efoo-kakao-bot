from __future__ import annotations

from datetime import date as DateType, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.restaurants import Restaurant


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
