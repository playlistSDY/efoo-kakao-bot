from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import Restaurant
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
