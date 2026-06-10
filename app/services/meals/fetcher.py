from datetime import date, datetime, timedelta
from typing import Dict, List
import logging
import re
import ssl
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib3 import poolmanager

from app.config import settings
from app.models import Meal, MealFetchLog, Restaurant
from app.domain.restaurants import get_restaurant_info

logger = logging.getLogger(__name__)


class SSLAdapter(HTTPAdapter):
    """Custom adapter for legacy TLS/cipher compatibility."""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.poolmanager = poolmanager.PoolManager(*args, ssl_context=context, **kwargs)


def create_ssl_session() -> requests.Session:
    session = requests.Session()
    adapter = SSLAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class HTMLParser:
    """한양대 식단 HTML 파서"""

    def parse_meal_html(self, html: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        restaurant_name = self._parse_restaurant_name(soup)
        parsed_date, day_of_week = self._parse_date_info(soup)
        meals = self._parse_meals(soup)
        return {
            "restaurant": restaurant_name,
            "date": parsed_date,
            "day_of_week": day_of_week,
            **meals,
        }

    def _parse_restaurant_name(self, soup: BeautifulSoup) -> str:
        restaurant_tag = soup.find("h3", class_="hyu-cafeName")
        if restaurant_tag:
            return restaurant_tag.get_text(strip=True)
        restaurant_tag = soup.find("strong", class_="font-point5")
        if restaurant_tag:
            return restaurant_tag.get_text(strip=True)
        return ""

    def _parse_date_info(self, soup: BeautifulSoup) -> tuple[str, str]:
        parsed_date = ""
        day_of_week = ""

        pagination_h1 = soup.select_one(".hyu-pagination-container h1")
        if pagination_h1:
            raw = pagination_h1.get_text(strip=True)
            normalized = re.sub(r"\s+", "", raw.replace("-", "/").replace(".", "/"))
            if re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", normalized):
                return normalized, self._day_of_week_from_date_str(normalized)

        day_selc = soup.find("div", class_="day-selc")
        if day_selc:
            date_tag = day_selc.find("strong")
            if date_tag:
                parsed_date = date_tag.get_text(strip=True)
            for span in day_selc.find_all("span"):
                if span.get_text(strip=True) and not span.get("class"):
                    day_of_week = span.get_text(strip=True)
                    break

        if parsed_date and not day_of_week:
            normalized = re.sub(r"\s+", "", parsed_date.replace("-", "/").replace(".", "/"))
            if re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", normalized):
                day_of_week = self._day_of_week_from_date_str(normalized)

        return parsed_date, day_of_week

    def _day_of_week_from_date_str(self, date_str: str) -> str:
        weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        try:
            parsed = datetime.strptime(date_str, "%Y/%m/%d")
            return weekdays[parsed.weekday()]
        except ValueError:
            return ""

    def _parse_meals(self, soup: BeautifulSoup) -> Dict[str, List[Dict]]:
        meals = {"조식": [], "중식": [], "석식": []}

        daily_view = soup.find("div", id=re.compile(r"dailyView"))
        if daily_view:
            meal_sections = daily_view.find_all("h3", class_="hyu-element")
            for header in meal_sections:
                title = header.get_text(strip=True)
                meal_type = self._normalize_meal_type(title)
                if not meal_type:
                    continue

                container = header.find_next_sibling("div", class_="hyu-list-container")
                if not container:
                    continue

                for card in container.select(".menu-thumbnail"):
                    parsed = self._parse_new_menu_card(card, meal_type)
                    if parsed:
                        meals[meal_type].append(parsed)

        for meal_type in ["조식", "중식", "석식"]:
            meals[meal_type] = self._remove_duplicate_meals(meals[meal_type])

        return meals

    def _normalize_meal_type(self, text: str) -> str:
        if "조식" in text:
            return "조식"
        if "중식" in text:
            return "중식"
        if "석식" in text:
            return "석식"
        return ""

    def _parse_new_menu_card(self, card, meal_type: str) -> Dict | None:
        detail_tag = card.select_one(".menu-detail p")
        menu_text = detail_tag.get_text(" ", strip=True) if detail_tag else ""

        if not menu_text:
            link = card.find("a")
            if link:
                menu_text = link.get("title", "").strip()

        if not menu_text or self._is_notice_text(menu_text):
            return None

        price_tag = card.select_one(".menu-price h3")
        price_text = price_tag.get_text(strip=True) if price_tag else ""

        image_url = ""
        image_tag = card.select_one(".menu-img")
        if image_tag:
            style = image_tag.get("style", "")
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if match:
                image_url = match.group(1).strip()

        if not image_url:
            link = card.find("a")
            if link:
                image_url = link.get("href", "").strip()

        if image_url.startswith("/"):
            image_url = "https://www.hanyang.ac.kr" + image_url
        image_url = self._normalize_image_url(image_url)

        korean_name = self._split_to_list(menu_text)
        if not self._is_valid_menu_list(korean_name):
            return None

        return {
            "korean": korean_name,
            "tags": self._extract_tags(menu_text),
            "price": price_text,
            "image": image_url,
            "meal_type": meal_type,
        }

    def _normalize_image_url(self, image_url: str) -> str:
        normalized = image_url.strip()
        if not normalized:
            return ""
        if any(marker in normalized.lower() for marker in ("no-img", "no_image", "noimage")):
            return ""
        return normalized

    def _is_notice_text(self, text: str) -> bool:
        notice_patterns = [
            r"운영합니다",
            r"코너만.*운영",
            r"금요일.*한.*코너만",
            r"휴무|휴업",
            r"문의.*전화",
            r"연락.*안내",
            r"공지.*알림",
        ]
        for pattern in notice_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _is_valid_menu_list(self, menu_list: List[str]) -> bool:
        if not menu_list:
            return False
        valid_items = [item for item in menu_list if len(item) >= 2]
        if len(valid_items) < len(menu_list) * 0.5:
            return False
        return not any(self._is_notice_text(item) for item in menu_list)

    def _extract_tags(self, text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r"\[([^\]]+)\]", text)

    def _split_to_list(self, text: str) -> List[str]:
        if not text:
            return []

        text_without_tags = re.sub(r"\[[^\]]+\]", "", text)

        if "\t" in text_without_tags:
            items = [item.strip() for item in re.split(r"\s*\t\s*", text_without_tags) if item.strip()]
            if items:
                return items

        if "\n" in text_without_tags:
            items = [item.strip() for item in text_without_tags.split("\n") if item.strip()]
            if len(items) > 1:
                return items

        items = [item.strip() for item in re.split(r"\s{2,}", text_without_tags) if item.strip()]
        if len(items) > 1:
            return items

        single = [item.strip() for item in text_without_tags.split() if item.strip()]
        return single if single else [text_without_tags.strip()]

    def _remove_duplicate_meals(self, meal_list: List[Dict]) -> List[Dict]:
        seen = set()
        unique_meals = []
        for meal in meal_list:
            key = str(meal.get("korean", []))
            if key in seen:
                continue
            seen.add(key)
            unique_meals.append(meal)
        return unique_meals


class MealService:
    """식단 페이지 요청 + 파싱"""

    def __init__(self):
        self.session = create_ssl_session()
        self.parser = HTMLParser()

    def build_meal_url(self, restaurant_code: str, year: int, month: int, day: int) -> tuple[str, dict[str, str]]:
        self._validate_params(restaurant_code, year, month, day)
        menu_date = f"{year:04d}/{month:02d}/{day:02d}"
        return (
            f"{settings.HANYANG_BASE_URL}/web/www/{restaurant_code}",
            {
                "p_p_id": "kr_ac_hanyang_cafe_web_portlet_CafePortlet",
                "p_p_lifecycle": "0",
                "p_p_state": "normal",
                "p_p_mode": "view",
                "_kr_ac_hanyang_cafe_web_portlet_CafePortlet_sMenuDate": menu_date,
                "_kr_ac_hanyang_cafe_web_portlet_CafePortlet_action": "view",
            },
        )

    def get_meal_html(self, restaurant_code: str, year: int, month: int, day: int) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3",
            "Connection": "keep-alive",
        }

        api_url, params = self.build_meal_url(restaurant_code, year, month, day)
        response = self.session.get(api_url, params=params, headers=headers)
        response.raise_for_status()
        return response.text

    def _validate_params(self, restaurant_code: str, year: int, month: int, day: int):
        if restaurant_code not in settings.RESTAURANT_CODES:
            raise ValueError(f"잘못된 식당 코드: {restaurant_code}")
        if year <= 0:
            raise ValueError("year는 0보다 커야 합니다.")
        if not (1 <= month <= 12):
            raise ValueError("month는 1~12 범위여야 합니다.")
        if not (1 <= day <= 31):
            raise ValueError("day는 1~31 범위여야 합니다.")


class MealFetcher:
    """식단 수집/저장 단일 엔트리 포인트"""

    def __init__(self):
        self.meal_service = MealService()

    def fetch_and_store_meals(self, db: Session):
        fetch_start_time = time.time()
        today = date.today()
        end_date = today + timedelta(days=settings.MEAL_FETCH_DAYS_AHEAD)

        total_saved = 0
        for restaurant_code, restaurant_name in settings.RESTAURANT_CODES.items():
            restaurant = self._get_or_create_restaurant(db, restaurant_code, restaurant_name)
            current_date = today
            while current_date <= end_date:
                total_saved += self._fetch_and_store_single_day(db, restaurant, restaurant_code, current_date)
                current_date += timedelta(days=1)

        logger.info(
            "급식 정보 수집 완료. 총 %s개 저장/갱신, 소요시간 %.2f초",
            total_saved,
            time.time() - fetch_start_time,
        )
        return total_saved

    def fetch_and_store_for_date(
        self,
        db: Session,
        target_date: date,
        restaurant_codes: list[str] | None = None,
    ) -> int:
        total_saved = 0
        codes = restaurant_codes or list(settings.RESTAURANT_CODES.keys())
        for restaurant_code in codes:
            restaurant_name = settings.RESTAURANT_CODES[restaurant_code]
            restaurant = self._get_or_create_restaurant(db, restaurant_code, restaurant_name)
            try:
                saved_count = self._fetch_and_store_single_day(db, restaurant, restaurant_code, target_date)
                self._upsert_fetch_log(db, restaurant.id, target_date, "success", f"{saved_count}개 저장/갱신")
                total_saved += saved_count
            except Exception as exc:
                self._upsert_fetch_log(db, restaurant.id, target_date, "failed", str(exc))
                logger.exception("%s %s 급식 정보 수집 실패", restaurant_code, target_date)
                raise
        return total_saved

    def _fetch_and_store_single_day(self, db: Session, restaurant: Restaurant, restaurant_code: str, target_date: date) -> int:
        html_content = self.meal_service.get_meal_html(
            restaurant_code,
            target_date.year,
            target_date.month,
            target_date.day,
        )
        meal_data = self.meal_service.parser.parse_meal_html(html_content)
        saved_count = 0

        for meal_type in ["조식", "중식", "석식"]:
            meals = meal_data.get(meal_type, [])
            existing_meals = db.scalars(
                select(Meal).where(
                    Meal.restaurant_id == restaurant.id,
                    Meal.date == target_date,
                    Meal.meal_type == meal_type,
                )
            ).all()

            if not meals:
                for existing_meal in existing_meals:
                    db.delete(existing_meal)
                db.commit()
                continue

            updated_existing_ids = set()
            for i, meal_item in enumerate(meals):
                existing_meal = existing_meals[i] if i < len(existing_meals) else None
                if existing_meal:
                    updated_existing_ids.add(existing_meal.id)
                    existing_meal.korean_name = meal_item.get("korean", [])
                    existing_meal.tags = meal_item.get("tags", [])
                    existing_meal.price = meal_item.get("price", "")
                    existing_meal.image_url = meal_item.get("image", "")
                    existing_meal.day_of_week = meal_data.get("day_of_week", "")
                else:
                    db.add(
                        Meal(
                            restaurant_id=restaurant.id,
                            date=target_date,
                            day_of_week=meal_data.get("day_of_week", ""),
                            meal_type=meal_type,
                            korean_name=meal_item.get("korean", []),
                            tags=meal_item.get("tags", []),
                            price=meal_item.get("price", ""),
                            image_url=meal_item.get("image", ""),
                        )
                    )
                saved_count += 1

            for existing_meal in existing_meals:
                if existing_meal.id not in updated_existing_ids:
                    db.delete(existing_meal)

            db.commit()

        return saved_count

    def _upsert_fetch_log(self, db: Session, restaurant_id: int, target_date: date, status: str, message: str) -> None:
        fetched_at = datetime.now(ZoneInfo(settings.APP_TIMEZONE))
        fetch_log = db.scalar(
            select(MealFetchLog).where(
                MealFetchLog.restaurant_id == restaurant_id,
                MealFetchLog.date == target_date,
            )
        )
        if not fetch_log:
            fetch_log = MealFetchLog(restaurant_id=restaurant_id, date=target_date, fetched_at=fetched_at, status=status)
            db.add(fetch_log)
        fetch_log.fetched_at = fetched_at
        fetch_log.status = status
        fetch_log.message = message[:500]
        db.commit()

    def _get_or_create_restaurant(self, db: Session, code: str, name: str) -> Restaurant:
        restaurant = db.scalar(select(Restaurant).where(Restaurant.code == code))
        info = get_restaurant_info(code)
        if not restaurant:
            restaurant = Restaurant(code=code, name=info.get("name", name))
            db.add(restaurant)

        if info:
            restaurant.name = info.get("name", restaurant.name)
            restaurant.address = info.get("location", restaurant.address)
            restaurant.building = info.get("building", restaurant.building)
            restaurant.floor = info.get("floor", restaurant.floor)
            restaurant.description = f"줄임말: {', '.join(info.get('aliases', []))}" if info.get("aliases") else restaurant.description
            restaurant.open_times = info.get("open_times", restaurant.open_times)

        db.commit()
        db.refresh(restaurant)
        return restaurant


meal_fetcher = MealFetcher()
