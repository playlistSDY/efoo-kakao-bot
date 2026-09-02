# 에푸: 툴을 사용하는 학식 카카오톡 에이전트

한양대학교 ERICA 학식, 식당 위치, 운영시간을 조회하고 카카오톡에 알맞은 형태로 답하는 FastAPI 백엔드입니다.

기존의 고정 LangGraph 흐름은 제거했습니다. OpenAI Responses API 모델이 대화를 읽고 필요한 툴을 0회 이상 호출한 뒤, 일반 텍스트·일반 카드·이미지 카드·캐러셀 중 표현 방식을 직접 계획합니다.

## 동작 방식

```text
카카오 요청
  → 사용자 프로필과 최근 대화 로드
  → Responses API 에이전트
      ↔ 현재 시각 툴
      ↔ 날짜별 학식 조회 툴
      ↔ 식당 정보 툴
  → 구조화된 카카오 응답 계획
  → 텍스트 / 카드 / 이미지 카드 / 캐러셀 렌더링
  → Callback API 전송
```

에이전트는 한 번의 툴 호출로 제한되지 않습니다. 예를 들어 “내일 점심이랑 모레 점심 비교해줘”는 날짜별 학식 툴을 여러 번 호출한 다음 결과를 합쳐 답할 수 있습니다. 최대 라운드는 `OPENAI_MAX_TOOL_ROUNDS`로 제한합니다.

## 모델

기본값은 `gpt-5.6-luna`, reasoning effort는 `low`입니다.

- 최신 GPT-5.6 계열의 툴 호출과 구조화 출력을 사용합니다.
- 비용이 중요한 고빈도 챗봇에 맞춘 기본값입니다.
- 더 높은 품질이 필요하면 `OPENAI_MODEL=gpt-5.6-terra`로 바꿀 수 있습니다.
- 모델과 reasoning effort는 코드 수정 없이 환경변수로 교체할 수 있습니다.

모델 가격과 지원 기능은 [OpenAI 모델 비교](https://developers.openai.com/api/docs/models/compare)에서 확인합니다.

## 에이전트 툴

### `get_current_datetime`

`Asia/Seoul` 기준 현재 날짜, 요일, 시각을 반환합니다.

### `get_meals`

정확히 한 날짜의 학식을 조회합니다.

- `date`: `YYYY-MM-DD`
- `restaurant_codes`: 특정 식당 코드 배열 또는 전체 식당을 뜻하는 `null`
- `meal_types`: `조식`, `중식`, `석식` 배열 또는 하루 전체를 뜻하는 `null`
- `refresh`: 최신 캐시 확인 여부

특정 식당만 요청하면 해당 식당만 크롤링하므로 불필요한 네트워크 요청을 줄입니다. 동일 날짜·식당의 성공한 조회가 30분 이내면 DB 캐시를 재사용합니다.

### `get_restaurant_info`

식당명, 줄임말, 위치, 운영시간과 오늘 현재 운영 상태를 반환합니다.

| 코드 | 식당 | 줄임말 | 위치 |
|---|---|---|---|
| `re11` | 교직원식당 | 교식 | 복지관 3층 |
| `re12` | 학생식당 | 학식 | 복지관 2층 |
| `re13` | 창의인재원식당 | 창의, 긱식 | 창의관 1층 |
| `re15` | 창업보육센터 | 창보 | 창업보육센터 지하 1층 |

## 카카오 출력

모델의 최종 출력은 JSON Schema로 강제됩니다.

- `message`: 카드 없이 읽어도 이해되는 최종 답변
- `presentation`: `simple_text`, `basic_card`, `carousel`
- `meal_ids`: 실제 툴 결과 중 카드에 사용할 메뉴 ID
- `quick_replies`: 최대 5개의 후속 질문

서버는 모델이 조회하지 않은 메뉴 ID를 무시하고 카카오 제약에 맞게 길이를 제한합니다. `basic_card`에 이미지 URL이 있으면 이미지 카드가 되고, 없으면 썸네일 없는 일반 카드가 됩니다.

## 실행

### Docker Compose

```bash
cp .env.example .env
# .env의 OPENAI_API_KEY를 실제 키로 변경
docker compose up -d --build
```

```bash
curl http://localhost:8000/health
docker compose logs -f chatbot
```

### 로컬

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# .env의 OPENAI_API_KEY를 실제 키로 변경
.venv/bin/python -m uvicorn app.main:app --reload
```

## 환경변수

```env
DATABASE_URL=sqlite:////data/efoo_chatbot.db
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=low
OPENAI_MAX_TOOL_ROUNDS=8
APP_TIMEZONE=Asia/Seoul
MEAL_FETCH_DAYS_AHEAD=7
HANYANG_BASE_URL=https://www.hanyang.ac.kr
HANYANG_RESTAURANTS=re11:교직원식당,re12:학생식당,re13:창의인재원식당,re15:창업보육센터
```

## 카카오 연동

카카오 i 오픈빌더 webhook은 다음 엔드포인트를 사용합니다.

```text
POST /kakao/callback
```

요청에 `userRequest.callbackUrl`이 있으면 서버는 먼저 `useCallback: true`를 반환하고 백그라운드에서 에이전트를 실행합니다. 실제 응답은 Callback API로 전송합니다. callback URL이 없는 로컬 요청은 같은 에이전트를 동기 실행합니다.

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
```

API를 직접 확인할 수도 있습니다.

```bash
curl --get 'http://localhost:8000/test/chat' \
  --data-urlencode 'message=9월 3일 학생식당 점심 알려줘'
```

`/test/chat` 응답에는 실행한 툴 이름·인자·결과, 에이전트 단계, 선택한 카카오 표현 방식과 최종 카카오 JSON이 포함됩니다.

## 주요 구조

```text
app/
  api/kakao.py                 # 카카오 webhook
  services/chatbot.py          # Responses API 반복 툴 에이전트
  services/chat_tools/tools.py # 날짜별 학식·식당·현재시각 툴
  services/kakao_templates.py  # 카카오 텍스트/카드/캐러셀 렌더러
  services/meals/              # 크롤러와 30분 캐시
  prompts/system.txt           # 에이전트 정책
  repositories/                # DB 접근
  models/                      # SQLAlchemy 모델
tests/
  test_agent_and_tools.py      # 툴 루프와 렌더러 테스트
```
