# 에푸: 툴을 사용하는 학식 카카오톡 에이전트

한양대학교 ERICA 학식, 식당 위치, 운영시간을 조회하고 카카오톡에 알맞은 형태로 답하는 FastAPI 백엔드입니다.

기존의 고정 LangGraph 흐름은 제거했습니다. OpenAI Responses API 모델이 대화를 읽고 필요한 툴을 0회 이상 호출한 뒤, 일반 텍스트·일반 카드·이미지 카드·캐러셀 중 표현 방식을 직접 계획합니다.

## 동작 방식

```text
카카오 요청
  → 사용자 프로필과 최근 대화 로드
  → 단순 메뉴 조회면 캐시에서 즉시 응답 (모델 호출 없음)
  → Responses API 에이전트
      ↔ 현재 시각 툴
      ↔ 날짜별 학식 조회 툴
      ↔ 식당 정보 툴
      ↔ 필요할 때만 과거 대화 회상 툴
  → 구조화된 카카오 응답 계획
  → 텍스트 / 카드 / 이미지 카드 / 캐러셀 렌더링
  → Callback API 전송
```

에이전트는 한 번의 툴 호출로 제한되지 않습니다. 예를 들어 “내일 점심이랑 모레 점심 비교해줘”는 날짜별 학식 툴을 여러 번 호출한 다음 결과를 합쳐 답할 수 있습니다. 최대 라운드는 `OPENAI_MAX_TOOL_ROUNDS`, 전체 실행시간은 `AGENT_TIME_BUDGET_SECONDS`로 제한합니다.

“오늘 중식 식당별로 정리해줘”처럼 독립적이고 명확한 메뉴 조회는 3초 이내 응답을 위해 모델을 거치지 않습니다. 서버가 날짜·식당·식사 종류를 직접 해석해 캐시와 기존 카카오 템플릿으로 즉시 답합니다. 이때 식당별 제공시간과 현재 시각을 비교해 운영 전·제공 중·마감 안내도 함께 표시합니다. 캐시가 한 번도 생성되지 않은 날짜는 느린 최초 크롤링을 백그라운드에 예약하고 재조회 버튼을 제공합니다. 추천·비교·개인화·후속 질문은 기존 에이전트가 처리합니다.

## 선택적 대화 기억

모든 과거 대화를 매번 프롬프트에 넣지 않습니다.

- “그거 교식은?”, “그러면 더 싼 메뉴는?”처럼 앞선 내용이 필요한 질문은 직전 대화를 이어서 처리합니다.
- “전에 추천했던 메뉴가 뭐였지?”처럼 오래된 대화를 명시하면 `recall_conversation` 툴로 해당 사용자의 기록을 검색합니다.
- “내일 학생식당 알려줘”처럼 그 자체로 완결된 질문은 이전 날짜·식당 조건을 물려받지 않고 새 요청으로 처리합니다.
- 현재 질문과 과거 조건이 충돌하면 현재 질문을 우선합니다.
- 다른 사용자의 기록은 조회 대상에 포함되지 않습니다.

디버그 응답과 저장된 assistant 메시지에는 문맥 판단 결과가 기록됩니다.

- `new`: 독립적인 새 요청
- `continuation`: 직전 대화의 후속 요청
- `recalled`: 과거 대화 검색 툴을 사용한 요청

## 모델

기본값은 `gpt-5.6-luna`입니다. 첫 툴 선택 라운드는 reasoning effort `none`, 툴 결과를 정리하는 후속 라운드는 `low`를 사용합니다.

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

특정 식당만 요청하면 해당 식당만 다룹니다. 메뉴 본문 캐시는 3시간 동안 즉시 재사용하고, 만료되면 기존 내용을 먼저 반환한 뒤 백그라운드에서 갱신합니다. DB에 해당 날짜의 조회 기록이 전혀 없는 최초 요청만 동기 크롤링합니다.

오늘 메뉴의 사진은 본문 캐시와 별개로 10분 간격의 백그라운드 확인 작업을 예약합니다. 따라서 메뉴 공개 직후 본문에 사진이 없더라도 이후 요청을 느리게 만들지 않으면서 새 사진을 발견할 수 있습니다.

### `get_restaurant_info`

식당명, 줄임말, 위치, 운영시간과 오늘 현재 운영 상태를 반환합니다.

| 코드 | 식당 | 줄임말 | 위치 |
|---|---|---|---|
| `re11` | 교직원식당 | 교식 | 복지관 3층 |
| `re12` | 학생식당 | 학식 | 복지관 2층 |
| `re13` | 창의인재원식당 | 창의, 긱식 | 창의관 1층 |
| `re15` | 창업보육센터 | 창보 | 창업보육센터 지하 1층 |

### `recall_conversation`

현재 메시지가 이전 대화에 의존할 때만 호출합니다. 같은 사용자의 저장된 대화에서 핵심어와 관련된 메시지를 최대 12개까지 가져옵니다. 직전 힌트만으로 충분하거나 독립적인 새 질문이라면 호출하지 않습니다.

## 카카오 출력

모델의 최종 출력은 JSON Schema로 강제됩니다.

- `message`: 카드 없이 읽어도 이해되는 최종 답변
- `presentation`: `simple_text`, `basic_card`, `carousel`
- `meal_ids`: 실제 툴 결과 중 카드에 사용할 메뉴 ID
- `quick_replies`: 최대 5개의 후속 질문

서버는 모델이 조회하지 않은 메뉴 ID를 무시하고 카카오 제약에 맞게 길이를 제한합니다. 실제 사진이 없으면 우리 도메인의 placeholder를 넣어 모든 `basicCard`가 카카오 말풍선 가이드를 만족하게 합니다.

## 이미지 캐시

`PUBLIC_BASE_URL`이 설정되면 카카오에는 학교 원본 주소 대신 다음처럼 우리 도메인의 안정적인 URL을 전달합니다.

```text
https://chatbot.example.com/media/meals/{sha256-key}
```

- `*.hanyang.ac.kr` 이미지만 허용해 임의 URL 프록시를 방지합니다.
- 이미지는 `/data/meal-images`에 백그라운드로 저장됩니다.
- 다운로드가 아직 끝나지 않았으면 같은 URL에서 검증된 학교 원본으로 잠시 리다이렉트합니다.
- 캐시가 준비되면 이후 요청은 우리 서버의 파일을 직접 반환합니다.
- Docker의 기존 `efoo-data` 볼륨을 사용하므로 컨테이너 재생성 후에도 유지됩니다.

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
OPENAI_TOOL_REASONING_EFFORT=none
OPENAI_REASONING_EFFORT=low
OPENAI_MAX_TOOL_ROUNDS=4
OPENAI_TIMEOUT_SECONDS=15
AGENT_TIME_BUDGET_SECONDS=40
MEAL_HTTP_CONNECT_TIMEOUT_SECONDS=2
MEAL_HTTP_READ_TIMEOUT_SECONDS=5
PUBLIC_BASE_URL=https://chatbot.example.com
MEAL_IMAGE_CACHE_DIR=/data/meal-images
MEAL_IMAGE_MAX_BYTES=10485760
MEAL_IMAGE_REFRESH_MINUTES=10
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

## 배포 후 응답이 멈출 때

현재 서버에 실제 적용된 모델과 시간 예산을 먼저 확인합니다.

```bash
curl https://내-서버-주소/health
```

정상 예시:

```json
{"status":"ok","model":"gpt-5.6-luna","agent_time_budget_seconds":40.0}
```

모델이 `gpt-4o-mini`처럼 예전 값이면 서버의 기존 `.env` 또는 배포 플랫폼 환경변수가 새 기본값을 덮어쓰고 있는 것입니다. 서버 환경변수를 변경한 뒤 컨테이너를 다시 생성합니다.

```bash
docker compose up -d --build --force-recreate
docker compose logs -f chatbot
```

`Responses API 에이전트 실행 실패` 로그가 있으면 바로 뒤의 OpenAI 오류 내용을 확인합니다. `학식 정보 수집 실패`라면 학교 사이트 연결 문제이며, 서버는 설정된 HTTP timeout 뒤 DB에 저장된 메뉴로 답변을 계속 시도합니다.

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
```

API를 직접 확인할 수도 있습니다.

```bash
curl --get 'http://localhost:8000/test/chat' \
  --data-urlencode 'message=9월 3일 학생식당 점심 알려줘'
```

`/test/chat` 응답에는 실행한 툴 이름·인자·결과, 에이전트 단계, `context_mode`, 선택한 카카오 표현 방식과 최종 카카오 JSON이 포함됩니다.

## 주요 구조

```text
app/
  api/kakao.py                 # 카카오 webhook
  services/chatbot.py          # Responses API 반복 툴 에이전트
  services/chat_tools/tools.py # 날짜별 학식·식당·현재시각 툴
  services/kakao_templates.py  # 카카오 텍스트/카드/캐러셀 렌더러
  services/meals/              # 크롤러, 3시간 본문 캐시, 이미지 캐시
  prompts/system.txt           # 에이전트 정책
  repositories/                # DB 접근
  models/                      # SQLAlchemy 모델
tests/
  test_agent_and_tools.py      # 툴 루프와 렌더러 테스트
```
