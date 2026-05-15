"""
Meal/Cafeteria schema guard script.

기능:
- `cafeteria`, `cafeteria_meals` 테이블이 없으면 생성
- 필수 컬럼이 없으면 ALTER TABLE로 추가
- 필수 인덱스가 없으면 생성

사용법:
    python scripts/ensure_meal_schema.py
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Iterable

from sqlalchemy import (
    JSON,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)

# 프로젝트 루트 경로 추가
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.config import settings


def _build_expected_tables(metadata: MetaData) -> tuple[Table, Table]:
    cafeteria = Table(
        "cafeteria",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(10), nullable=False),
        Column("name", String(100), nullable=False),
        Column("address", String(200), nullable=True),
        Column("building", String(50), nullable=True),
        Column("floor", String(20), nullable=True),
        Column("latitude", String(20), nullable=True),
        Column("longitude", String(20), nullable=True),
        Column("description", String(500), nullable=True),
        Column("open_times", JSON, nullable=True),
    )

    cafeteria_meals = Table(
        "cafeteria_meals",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("restaurant_id", Integer, ForeignKey("cafeteria.id"), nullable=False),
        Column("date", Date, nullable=False),
        Column("day_of_week", String(10), nullable=True),
        Column("meal_type", String(10), nullable=False),
        Column("korean_name", JSON, nullable=False),
        Column("tags", JSON, nullable=True),
        Column("price", String(20), nullable=True),
        Column("image_url", String(500), nullable=True),
    )

    # 모델과 동일한 인덱스 정의
    Index("idx_restaurant_date", cafeteria_meals.c.restaurant_id, cafeteria_meals.c.date)
    Index(
        "idx_restaurant_date_type",
        cafeteria_meals.c.restaurant_id,
        cafeteria_meals.c.date,
        cafeteria_meals.c.meal_type,
    )

    return cafeteria, cafeteria_meals


def _column_exists(existing_columns: set[str], col_name: str) -> bool:
    return col_name.lower() in {c.lower() for c in existing_columns}


def _render_add_column_sql(table_name: str, column: Column, dialect) -> str:
    col_type = column.type.compile(dialect=dialect)
    # 기존 데이터가 있는 테이블에서도 실패하지 않도록 우선 NULL 허용으로 추가한다.
    return f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type} NULL"


def _sync_missing_columns(conn, table: Table):
    inspector = inspect(conn)
    table_name = table.name
    existing_cols = {col["name"] for col in inspector.get_columns(table_name)}

    for col in table.columns:
        if _column_exists(existing_cols, col.name):
            continue

        sql = _render_add_column_sql(table_name, col, conn.dialect)
        conn.execute(text(sql))
        print(f"[ADD COLUMN] {table_name}.{col.name}")


def _sync_missing_indexes(conn, table: Table):
    inspector = inspect(conn)
    table_name = table.name
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}

    for idx in table.indexes:
        if idx.name in existing_indexes:
            continue

        columns_csv = ", ".join([c.name for c in idx.columns])
        conn.execute(text(f"CREATE INDEX {idx.name} ON {table_name} ({columns_csv})"))
        print(f"[ADD INDEX] {idx.name} on {table_name}({columns_csv})")


def _print_summary(conn, tables: Iterable[Table]):
    inspector = inspect(conn)
    print("\n=== Schema Summary ===")
    for table in tables:
        cols = [c["name"] for c in inspector.get_columns(table.name)]
        print(f"- {table.name}: {', '.join(cols)}")


def ensure_meal_schema():
    metadata = MetaData()
    cafeteria, cafeteria_meals = _build_expected_tables(metadata)

    # 1) 테이블 생성 (없는 경우만)
    metadata.create_all(bind=engine, tables=[cafeteria, cafeteria_meals], checkfirst=True)

    # 2) 컬럼/인덱스 보정
    with engine.begin() as conn:
        _sync_missing_columns(conn, cafeteria)
        _sync_missing_columns(conn, cafeteria_meals)

        _sync_missing_indexes(conn, cafeteria_meals)

        _print_summary(conn, [cafeteria, cafeteria_meals])


if __name__ == "__main__":
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    ensure_meal_schema()
