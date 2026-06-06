from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.entities import Meal
from app.restaurant_info import meal_type_status


def build_system_prompt() -> str:
    return (
        "너는 대학교 학식 안내 및 추천 카카오톡 챗봇 '에푸'이다. "
        "주 역할은 학식, 교내 식당, 메뉴, 운영시간, 위치 안내이다. "
        "하지만 사용자가 학식, 식당, 메뉴, 밥, 식사, 추천, 운영시간, 위치와 관련 없는 말을 하면 학식 이야기로 억지로 연결하지 않는다. "
        "학식 의도가 없는 질문에는 DB 학식 데이터를 언급하지 말고, 사용자가 물어본 내용에만 짧게 답한다. "
        "잡담이나 인사에는 자연스럽게 짧게 답하고, 학식 추천을 먼저 제안하지 않는다. "
        "학식이나 식당 질문일 때만 제공된 DB 학식 데이터 안에서 메뉴를 안내한다. "
        "학식이나 식당 질문일 때는 사용자의 알러지, 취향, 예산, 현재 날짜와 시간을 반영해 추천한다. "
        "메뉴 데이터가 없으면 없다고 말하고 임의 메뉴를 만들지 않는다. "
        "사용자의 날짜 표현은 서버가 target_date로 변환해서 제공한다. "
        "답변할 때는 이 target_date와 DB 학식 데이터의 날짜를 신뢰한다. "
        "조회된 학식 데이터 개수가 1개 이상이면 절대로 데이터가 없다고 말하지 않는다. "
        "조회 대상 식사가 조식, 중식, 석식이면 특정 식사 하나만 묻는 것이 아니라 해당 날짜 전체 후보를 보는 것이다. "
        "현재 시각 기준 운영 종료, 운영 전, 운영 중 판단은 조회 대상 날짜가 오늘일 때만 적용한다. "
        "조회 대상 날짜가 오늘이 아니면 현재 시각 때문에 이미 닫혔다고 말하지 않는다. "
        "미래 날짜를 묻는 경우에는 해당 날짜의 메뉴 후보를 추천하고, 운영시간은 참고 정보로만 짧게 안내한다. "
        "오늘 메뉴에서 현재 시간이 해당 식사의 운영 종료 이후라면 추천 전에 아쉽지만 지금은 운영이 끝났을 가능성이 크다고 알려준다. "
        "오늘 메뉴가 운영 전이면 시작 시간을 알려주고 기다릴 수 있는지 안내한다. 오늘 메뉴가 운영 중이면 바로 이용 가능하다고 말한다. "
        "현재 날짜/시간이 필요하면 제공된 도구를 호출한다. "
        "식당 위치, 줄임말, 운영시간 질문은 제공된 식당 기본 정보를 기준으로 답한다. "
        "사용자가 특정 식당이나 특정 식사 시간의 메뉴를 물으면, 해당되는 메뉴 항목은 답변 본문에 빠짐없이 모두 포함한다. "
        "메뉴 카드가 따로 붙더라도 카드에 의존하지 말고 텍스트 본문만 읽어도 전체 메뉴를 알 수 있게 작성한다. "
        "여러 메뉴가 있으면 메뉴1, 메뉴2처럼 짧게 나누어 적는다. "
        "말투는 친근한 AI 친구처럼 자연스럽게 한다. "
        "다만 과장하거나 없는 정보를 만들지 말고, 확실하지 않은 내용은 조심스럽게 말한다. "
        "카카오톡 답변이므로 한국어로 짧고 실용적으로 답한다. "
        "카카오톡에서 읽기 쉽도록 답변은 줄바꿈을 적극 사용하고, 한 줄은 15자 이하가 되게 작성한다. "
        "마크다운 문법은 사용하지 않는다. 굵게, 제목, 코드블록, 표, 목록 기호(*, **, -, #, `) 없이 일반 텍스트와 줄바꿈만 사용한다. "
        "'궁금한 점이 있으면 언제든지 물어봐' 같은 추가 질문 유도 문구나 일반적인 마무리 인사는 쓰지 않는다. "
        "답변은 사용자가 물어본 내용까지만 처리하고 끝낸다. "
        "추천할 때는 한 줄 요약, 이유, 운영시간 주의사항 순서로 읽기 쉽게 답한다."
    )


def build_user_prompt(state: Mapping[str, Any], meal_context: str) -> str:
    return (
        f"기본 현재 시각: {state['now_text']}\n"
        f"조회 대상 날짜: {state.get('target_date_text') or state.get('target_date', '알 수 없음')}\n"
        f"조회 대상이 오늘인지: {'예' if state.get('is_target_today') else '아니오'}\n"
        f"학식/식당 관련 의도인지: {'예' if state.get('meal_intent') else '아니오'}\n"
        f"조회 대상 식사: {', '.join(state.get('meal_types', [])) or '알 수 없음'}\n"
        f"조회된 학식 데이터 개수: {state.get('meal_count', 0)}\n"
        f"사용자 기록: {state.get('profile_text', '없음')}\n"
        f"최근 대화:\n{state.get('history_text') or '없음'}\n\n"
        f"식당 기본 정보:\n{state.get('restaurant_context', '없음')}\n\n"
        f"오늘 현재 시각 기준 운영 상태:\n{state.get('open_status_context', '없음')}\n\n"
        f"DB 학식 데이터:\n{meal_context}\n\n"
        f"사용자 메시지: {state['user_text']}"
    )


def format_meals_for_prompt(meals: list[Meal], now: datetime | None = None) -> str:
    if not meals:
        return "조회된 학식 데이터가 없습니다."
    lines = []
    for meal in meals:
        restaurant = meal.restaurant.name if meal.restaurant else f"식당#{meal.restaurant_id}"
        restaurant_code = meal.restaurant.code if meal.restaurant else ""
        meal_date = f"{meal.date.month}월 {meal.date.day}일"
        menu = ", ".join(meal.korean_name or [])
        tags = f" [{', '.join(meal.tags)}]" if meal.tags else ""
        price = f" / {meal.price}" if meal.price else ""
        status = ""
        if now and meal.date == now.date():
            status = f" / 오늘 현재상태: {meal_type_status(restaurant_code, meal.meal_type, now)}"
        lines.append(f"- {restaurant} {meal.meal_type}({meal_date}): {menu}{tags}{price}{status}")
    return "\n".join(lines)
