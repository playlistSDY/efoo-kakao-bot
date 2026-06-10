from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.db import SessionLocal, init_db
from app.services.meals.fetcher import meal_fetcher


def main():
    init_db()
    db = SessionLocal()
    try:
        count = meal_fetcher.fetch_and_store_meals(db)
        print(f"saved_or_updated={count}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
