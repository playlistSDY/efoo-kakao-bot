from sqlalchemy import Engine, inspect, text

from app.db.base import Base
from app.db.session import engine


def init_db() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_user_profile_columns(engine)


def ensure_user_profile_columns(target_engine: Engine) -> None:
    inspector = inspect(target_engine)
    if "user_profiles" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("user_profiles")}
    additions = {
        "nickname": "VARCHAR(40)",
        "speech_style": "VARCHAR(20)",
        "conversation_preferences": "JSON",
    }
    with target_engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in existing:
                connection.execute(text(f'ALTER TABLE user_profiles ADD COLUMN "{column_name}" {column_type}'))
