from __future__ import annotations

from datetime import date

from sqlalchemy import delete, distinct, select
from sqlalchemy.orm import Session

from app.entities import Meal, Restaurant
from app.repositories.restaurants import get_restaurant_by_code


def create_meal(
    db: Session,
    restaurant_id: int,
    date: date,
    day_of_week: str,
    meal_type: str,
    korean_name: list[str],
    tags: list[str],
    price: str,
    image_url: str,
) -> Meal:
    meal = Meal(
        restaurant_id=restaurant_id,
        date=date,
        day_of_week=day_of_week,
        meal_type=meal_type,
        korean_name=korean_name,
        tags=tags,
        price=price,
        image_url=image_url,
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return meal


def get_meals_by_date(db: Session, restaurant_code: str, target_date: date) -> list[Meal]:
    return list(
        db.scalars(
            select(Meal)
            .join(Restaurant, Meal.restaurant_id == Restaurant.id)
            .where(Restaurant.code == restaurant_code, Meal.date == target_date)
        )
    )


def get_meal_by_id(db: Session, meal_id: int) -> Meal | None:
    return db.get(Meal, meal_id)


def get_available_dates(db: Session, restaurant_code: str | None = None) -> list[str]:
    stmt = select(distinct(Meal.date))
    if restaurant_code:
        restaurant = get_restaurant_by_code(db, restaurant_code)
        if not restaurant:
            return []
        stmt = stmt.where(Meal.restaurant_id == restaurant.id)
    return [str(meal_date) for meal_date in db.scalars(stmt.order_by(Meal.date))]


def delete_meals_by_date_range(db: Session, restaurant_id: int, start_date: date, end_date: date):
    db.execute(
        delete(Meal).where(
            Meal.restaurant_id == restaurant_id,
            Meal.date >= start_date,
            Meal.date <= end_date,
        )
    )
    db.commit()


def get_meals_flexible(
    db: Session,
    target_date: date,
    restaurant_codes: list[str] | None = None,
    meal_types: list[str] | None = None,
) -> list[Meal]:
    stmt = (
        select(Meal)
        .join(Restaurant, Meal.restaurant_id == Restaurant.id)
        .where(Meal.date == target_date)
    )
    if restaurant_codes:
        stmt = stmt.where(Restaurant.code.in_(restaurant_codes))
    if meal_types:
        stmt = stmt.where(Meal.meal_type.in_(meal_types))
    return list(db.scalars(stmt))
