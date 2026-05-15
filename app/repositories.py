from __future__ import annotations

import re
from datetime import date

from sqlalchemy import delete, distinct, select
from sqlalchemy.orm import Session

from app.entities import ChatMessage, ChatSession, Meal, Restaurant, UserProfile
from app.restaurant_info import get_restaurant_info


def get_all_restaurants(db: Session) -> list[Restaurant]:
    return list(db.scalars(select(Restaurant)))


def get_restaurant_by_code(db: Session, code: str) -> Restaurant | None:
    return db.scalar(select(Restaurant).where(Restaurant.code == code))


def create_restaurant(db: Session, code: str, name: str) -> Restaurant:
    info = get_restaurant_info(code)
    restaurant = Restaurant(code=code, name=info.get("name", name))
    _apply_restaurant_info(restaurant, info)
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


def get_or_create_restaurant(db: Session, code: str, name: str) -> Restaurant:
    restaurant = get_restaurant_by_code(db, code)
    if restaurant:
        sync_restaurant_info(db, restaurant)
        return restaurant
    return create_restaurant(db, code, name)


def sync_restaurant_info(db: Session, restaurant: Restaurant) -> Restaurant:
    info = get_restaurant_info(restaurant.code)
    if not info:
        return restaurant
    _apply_restaurant_info(restaurant, info)
    db.commit()
    db.refresh(restaurant)
    return restaurant


def _apply_restaurant_info(restaurant: Restaurant, info: dict) -> None:
    if not info:
        return
    restaurant.name = info.get("name", restaurant.name)
    restaurant.address = info.get("location", restaurant.address)
    restaurant.building = info.get("building", restaurant.building)
    restaurant.floor = info.get("floor", restaurant.floor)
    restaurant.description = f"줄임말: {', '.join(info.get('aliases', []))}" if info.get("aliases") else restaurant.description
    restaurant.open_times = info.get("open_times", restaurant.open_times)


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


def get_or_create_user(db: Session, kakao_user_id: str) -> UserProfile:
    user = db.scalar(select(UserProfile).where(UserProfile.kakao_user_id == kakao_user_id))
    if user:
        return user
    user = UserProfile(kakao_user_id=kakao_user_id, allergies=[], preferences=[], dislikes=[])
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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
        tail = text.split(keyword, 1)[1]
        tail = re.sub(r"(있어|있음|야|입니다|이에요|예요|해|해요|함|이야)", " ", tail)
        return [item.strip(" ,./") for item in re.split(r"[,/와과랑및 ]+", tail) if len(item.strip(" ,./")) >= 2][:6]
    return []


def _extract_budget(text: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*(천원|만원|원)", text)
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
