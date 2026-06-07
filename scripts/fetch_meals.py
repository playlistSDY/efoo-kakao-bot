"""
특정 연도/월의 전체 급식 데이터 수집 스크립트

사용법:
    python scripts/fetch_meals.py 2025 9
    python scripts/fetch_meals.py 2024 12

설명:
    지정한 연도와 월의 모든 급식 데이터를 한양대 서버에서 가져와 DB에 저장합니다.
    이미 존재하는 데이터는 업데이트됩니다.
"""

import sys
import os
from datetime import date, timedelta
from calendar import monthrange
from pathlib import Path
import logging

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import SessionLocal
from app.services.meals.fetcher import HTMLParser, MealService
from app.config import settings
from app import repositories as crud_meal
from app.models import Meal, Restaurant, Rating, Keyword, MealKeywordReview

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_meals_for_month(year: int, month: int) -> dict:
    """
    특정 연도/월의 전체 급식 데이터 수집
    
    Args:
        year: 연도 (예: 2025)
        month: 월 (1-12)
    
    Returns:
        수집 결과 통계 딕셔너리
    """
    db = SessionLocal()
    meal_service = MealService()
    html_parser = HTMLParser()
    
    # 해당 월의 마지막 날짜 계산
    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    
    logger.info("=" * 70)
    logger.info(f"📅 {year}년 {month}월 급식 정보 수집 시작")
    logger.info("=" * 70)
    logger.info(f"수집 기간: {start_date} ~ {end_date} (총 {last_day}일)")
    logger.info("")
    
    # 통계 변수
    stats = {
        "total_saved": 0,
        "total_updated": 0,
        "total_errors": 0,
        "restaurants": {}
    }
    
    try:
        # 모든 식당에 대해 반복
        for restaurant_code, restaurant_name in settings.RESTAURANT_CODES.items():
            logger.info(f"\n{'=' * 70}")
            logger.info(f"🍽️  [{restaurant_name}] 데이터 수집 시작")
            logger.info(f"{'=' * 70}")
            
            restaurant_saved = 0
            restaurant_updated = 0
            restaurant_errors = 0
            
            try:
                # 식당 정보 가져오기 또는 생성
                restaurant = crud_meal.get_or_create_restaurant(
                    db, restaurant_code, restaurant_name
                )
                
                # 날짜별로 데이터 수집
                current_date = start_date
                while current_date <= end_date:
                    try:
                        day_name = current_date.strftime('%A')
                        logger.info(f"📆 {current_date.strftime('%Y-%m-%d')} ({day_name})")
                        
                        # 한양대 서버에서 HTML 가져오기
                        html_content = meal_service.get_meal_html(
                            restaurant_code,
                            current_date.year,
                            current_date.month,
                            current_date.day
                        )
                        
                        # HTML 파싱
                        meal_data = html_parser.parse_meal_html(html_content)
                        
                        # 각 식사 종류별로 저장
                        day_saved = 0
                        day_updated = 0
                        
                        for meal_type in ["조식", "중식", "석식"]:
                            meals = meal_data.get(meal_type, [])
                            
                            if not meals:
                                continue
                            
                            for meal_item in meals:
                                try:
                                    # 중복 체크
                                    new_korean = (
                                        meal_item["korean"] 
                                        if isinstance(meal_item["korean"], list) 
                                        else [meal_item["korean"]]
                                    )
                                    
                                    existing = db.query(Meal).filter(
                                        Meal.restaurant_id == restaurant.id,
                                        Meal.date == current_date,
                                        Meal.meal_type == meal_type,
                                        Meal.korean_name == new_korean
                                    ).first()
                                    
                                    # 중복이 없으면 새로운 메뉴 생성, 있으면 업데이트
                                    if not existing:
                                        crud_meal.create_meal(
                                            db=db,
                                            restaurant_id=restaurant.id,
                                            date=current_date,
                                            day_of_week=meal_data.get("day_of_week", ""),
                                            meal_type=meal_type,
                                            korean_name=meal_item["korean"],
                                            tags=meal_item.get("tags", []),
                                            price=meal_item.get("price", ""),
                                            image_url=meal_item.get("image", "")
                                        )
                                        day_saved += 1
                                        restaurant_saved += 1
                                        stats["total_saved"] += 1
                                    else:
                                        # 기존 메뉴 정보 업데이트
                                        existing.tags = meal_item.get("tags", [])
                                        existing.price = meal_item.get("price", "")
                                        existing.image_url = meal_item.get("image", "")
                                        existing.day_of_week = meal_data.get("day_of_week", "")
                                        db.commit()
                                        day_updated += 1
                                        restaurant_updated += 1
                                        stats["total_updated"] += 1
                                
                                except Exception as e:
                                    logger.error(f"   ❌ 메뉴 저장 실패: {e}")
                                    logger.debug(f"   메뉴 데이터: {meal_item}")
                                    restaurant_errors += 1
                                    stats["total_errors"] += 1
                        
                        # 해당 날짜 처리 결과 출력
                        if day_saved > 0 or day_updated > 0:
                            logger.info(f"   ✓ 신규 {day_saved}개, 업데이트 {day_updated}개")
                    
                    except Exception as e:
                        logger.error(f"   ❌ 날짜 {current_date} 처리 실패: {e}")
                        restaurant_errors += 1
                        stats["total_errors"] += 1
                    
                    current_date += timedelta(days=1)
                
                # 식당별 통계 저장
                stats["restaurants"][restaurant_name] = {
                    "saved": restaurant_saved,
                    "updated": restaurant_updated,
                    "errors": restaurant_errors
                }
                
                logger.info(f"\n✅ [{restaurant_name}] 완료")
                logger.info(f"   신규: {restaurant_saved}개")
                logger.info(f"   업데이트: {restaurant_updated}개")
                if restaurant_errors > 0:
                    logger.warning(f"   오류: {restaurant_errors}개")
            
            except Exception as e:
                logger.error(f"\n❌ [{restaurant_name}] 오류 발생: {e}")
                stats["total_errors"] += 1
                import traceback
                logger.debug(traceback.format_exc())
        
        # 최종 통계 출력
        logger.info("\n" + "=" * 70)
        logger.info(f"🎉 {year}년 {month}월 급식 정보 수집 완료!")
        logger.info("=" * 70)
        logger.info(f"\n📊 전체 통계:")
        logger.info(f"   총 신규 저장: {stats['total_saved']}개")
        logger.info(f"   총 업데이트: {stats['total_updated']}개")
        logger.info(f"   총 오류: {stats['total_errors']}개")
        
        if stats["restaurants"]:
            logger.info(f"\n📋 식당별 통계:")
            for restaurant_name, restaurant_stats in stats["restaurants"].items():
                logger.info(f"   [{restaurant_name}]")
                logger.info(f"      신규: {restaurant_stats['saved']}개")
                logger.info(f"      업데이트: {restaurant_stats['updated']}개")
                if restaurant_stats['errors'] > 0:
                    logger.info(f"      오류: {restaurant_stats['errors']}개")
        
        logger.info("")
        
    except Exception as e:
        logger.error(f"\n❌ 스크립트 실행 중 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        db.close()
        logger.info("DB 연결 종료\n")
    
    return stats


def validate_arguments(year: int, month: int) -> bool:
    """입력 인자 검증"""
    current_year = date.today().year
    
    # 연도 검증 (2020년 ~ 현재 연도 + 1년)
    if year < 2020 or year > current_year + 1:
        logger.error(f"❌ 잘못된 연도: {year}")
        logger.info(f"   유효 범위: 2020 ~ {current_year + 1}")
        return False
    
    # 월 검증 (1-12)
    if month < 1 or month > 12:
        logger.error(f"❌ 잘못된 월: {month}")
        logger.info("   유효 범위: 1 ~ 12")
        return False
    
    return True


def main():
    """메인 함수"""
    # 사용법 출력
    if len(sys.argv) != 3:
        logger.info("=" * 70)
        logger.info("📖 급식 데이터 수집 스크립트")
        logger.info("=" * 70)
        logger.info("\n사용법:")
        logger.info("   python scripts/fetch_meals.py [연도] [월]")
        logger.info("\n예제:")
        logger.info("   python scripts/fetch_meals.py 2025 9")
        logger.info("   python scripts/fetch_meals.py 2024 12")
        logger.info("")
        sys.exit(1)
    
    # 인자 파싱
    try:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    except ValueError:
        logger.error(f"❌ 잘못된 형식: {sys.argv[1]} {sys.argv[2]}")
        logger.info("   연도와 월은 정수여야 합니다.")
        logger.info("   예: python scripts/fetch_meals.py 2025 9")
        sys.exit(1)
    
    # 인자 검증
    if not validate_arguments(year, month):
        sys.exit(1)
    
    # 데이터 수집 실행
    logger.info(f"\n🚀 {year}년 {month}월 데이터 수집을 시작합니다...\n")
    stats = fetch_meals_for_month(year, month)
    
    # 종료 코드 결정
    if stats["total_errors"] > 0:
        logger.warning(f"⚠️  경고: {stats['total_errors']}개의 오류가 발생했습니다.")
        sys.exit(1)
    else:
        logger.info("✅ 모든 작업이 성공적으로 완료되었습니다!")
        sys.exit(0)


if __name__ == "__main__":
    main()
