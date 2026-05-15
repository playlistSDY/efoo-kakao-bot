# Efoo 학식 추천 카카오톡 챗봇

LangChain/LangGraph와 OpenAI API를 사용하는 학식 안내 및 추천용 카카오톡 챗봇 백엔드입니다.

## 기능

- 매일 `Asia/Seoul` 기준 00:00에 한양대 학식 정보를 크롤링해 DB 저장
- 카카오톡 유저별 프로필, 채팅 세션, 대화 메시지 저장
- 기존 알러지, 취향, 예산 기록과 현재 날짜/시간을 반영한 학식 안내
- 챗봇이 필요할 때 현재 날짜/시간, 현재 날씨 도구 호출
- LangGraph 기반 응답 플로우와 OpenAI Chat API 사용

## Docker Compose 실행

```bash
cp .env.example .env
# .env의 OPENAI_API_KEY를 설정
docker compose up -d --build
```

서버는 다음 주소로 실행됩니다.

```text
http://localhost:8000
```

상태 확인:

```bash
curl http://localhost:8000/health
```

로그 확인:

```bash
docker compose logs -f chatbot
```

수동 크롤링:

```bash
docker compose exec chatbot python scripts/fetch_today_meals.py
```

카카오 i 오픈빌더 webhook URL은 다음 엔드포인트로 연결합니다.

```text
POST /kakao/callback
```

AI 응답은 카카오 Callback API를 지원합니다.

- 카카오 요청의 `userRequest.callbackUrl`이 있으면 서버는 5초 안에 `{"version":"2.0","useCallback":true}`를 먼저 반환합니다.
- 실제 OpenAI 응답, 메뉴 카드, 캐러셀은 백그라운드에서 `callbackUrl`로 POST 전송합니다.
- `callbackUrl`이 없는 테스트 요청은 기존처럼 동기 응답을 바로 반환합니다.

SQLite DB는 Docker volume `efoo-data`의 `/data/efoo_chatbot.db`에 저장됩니다.

한양대 학식 크롤링 식당 코드는 `.env`의 `HANYANG_RESTAURANTS`로 설정합니다.

```env
HANYANG_RESTAURANTS=re11:교직원식당,re12:학생식당,re13:창의인재원식당,re15:창업보육센터
```

현재 사용하는 식당 코드는 다음입니다.

- `re11`: 교직원식당, 줄임말 `교식`, 복지관 3층, 중식 11:30-13:30
- `re12`: 학생식당, 줄임말 `학식`, 복지관 2층, 조식 08:30-09:40, 중식 11:30-13:30
- `re13`: 창의인재원식당, 줄임말 `창의`, `창의인재`, `긱식`, `기숙사식당`, 창의관 1층, 조식 07:40-09:00, 중식 11:30-13:20, 석식 17:10-18:40
- `re15`: 창업보육센터, 줄임말 `창보`, 창업보육센터 지하 1층, 중식 11:30-13:30, 석식 17:00-18:30

파서는 식당별로 `조식`, `중식`, `석식`을 각각 리스트로 저장합니다. 특정 식사 시간이 없는 날은 빈 리스트로 남고, 같은 식사 시간에 메뉴가 여러 개면 여러 `Meal` row로 저장됩니다.

## 챗봇 도구

`app/tools.py`에 LangChain tool이 정의되어 있습니다.

- `get_current_datetime`: `Asia/Seoul` 기준 현재 날짜, 요일, 시간 조회
- `get_current_weather`: Open-Meteo API로 현재 날씨 조회

사용자가 "오늘 몇 시야?", "지금 비 와?", "날씨 고려해서 점심 추천해줘"처럼 물으면 OpenAI 모델이 필요한 도구를 선택해 호출합니다. 기본 날씨 위치는 `.env`의 `DEFAULT_WEATHER_LOCATION` 값입니다.

## 로컬 실행

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# .env의 OPENAI_API_KEY를 설정
.venv/bin/python -m uvicorn app.main:app --reload
```

수동 크롤링은 다음 명령으로 실행합니다.

```bash
.venv/bin/python scripts/fetch_today_meals.py
```
