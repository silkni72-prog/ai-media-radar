# AI × MEDIA DAILY RADAR (MVP)

전 세계 AI 산업과 언론·미디어 산업 동향을 RSS·공식 API로 모아 LLM으로 분류·요약·채점하고,
매일 09:00(KST) 한국어 브리핑을 ntfy 푸시로 보낸다. WIRED는 별도로 30분마다 감시해
80점 이상 새 기사를 즉시 알린다.

**아침 브리핑 구성** (섹션마다 중요도 순 5~10개)

1. **① AI 최신 동향**: 모델·제품·투자 소식과 바이브코딩·코딩 에이전트 같은 실전 활용 소식. 실전 활용(🛠)은 최대 4자리를 보장한다
2. **② 언론 동향**: 언론사·플랫폼·검색 트래픽·저작권. AI 관련 소식을 우선한다
3. **③ WIRED 주요 이슈**: AI·Business·Backchannel·Top Stories. 쇼핑·할인 글은 뺀다

맨 위에는 '핵심 3줄'과 섹션별 제목만 모은 '한눈에 보기'가 붙는다. 섹션이 5개가 안 되면 집계 구간을 48→72시간으로 넓힌다(주말 대비). 25점 미만(WIRED는 15점 미만) 기사로는 채우지 않는다.

```
수집(RSS/API/허용된 HTML) → URL 정규화·중복 제거 → 키워드 1차 필터
  → LLM 분석(Pydantic 검증 + 근거 검증) → 이슈 묶기 → SQLite
  → 일일 브리핑(Markdown/HTML) / WIRED 즉시 알림 / 90점 이상 속보 → ntfy
```

## 구조

```
config/sources.yaml        출처 목록과 활성화 상태, 접근 확인 메모
src/collectors/            http(재시도) · rss · html_list(robots.txt 확인) · normalize(URL·제목 지문) · registry
src/analysis/              llm(제공자 추상화) · analyzer(프롬프트·검증) · validate(근거 검증)
                           heuristic(LLM 없을 때 규칙) · keywords · clustering(이슈 묶기·다양성)
src/briefing/              builder(선별·종합) · render(Markdown/HTML/푸시 본문)
src/notifications/ntfy.py  발송·재시도·중복 방지
src/storage/               db(SQLAlchemy 테이블) · repository(DB 접근 계층)
src/pipeline.py            수집→분석→알림 흐름, WIRED 모니터
src/main.py                명령행 진입점
.github/workflows/radar.yml  예약 실행
```

## 1. 설치

```bash
python -m venv .venv
```
```bash
.venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
```
```bash
pip install -r requirements.txt
```
```bash
cp .env.example .env
```

Python 3.12 이상.

## 2. 환경변수

`.env.example`에 전체 목록과 설명이 있다. 핵심만:

| 변수 | 설명 |
|---|---|
| `LLM_PROVIDER` | `anthropic` / `openai` / `none`. `none`이면 키워드 규칙으로만 분류·채점하고 번역·요약은 하지 않는다 |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 제공자 키 |
| `LLM_MODEL`, `LLM_BRIEFING_MODEL` | 비우면 기사 분석 `claude-haiku-5-5`, 브리핑 종합 `claude-sonnet-5-5` |
| `NTFY_BASE_URL` | 기본 `https://ntfy.sh` |
| `NTFY_DAILY_TOPIC` / `NTFY_WIRED_TOPIC` / `NTFY_ALERT_TOPIC` | 정기 브리핑 / WIRED / 속보 토픽 |
| `NTFY_TOKEN` | 접근 제어 토픽용 토큰(선택) |
| `NTFY_DRY_RUN` | **기본 `true`** — 실제 발송 없이 로그·DB에만 기록. 확인 후 `false` |
| `BRIEFING_PUBLIC_BASE_URL` | 브리핑 HTML 공개 주소. 있으면 푸시를 누르면 전체 브리핑이 열린다 |
| `DATABASE_URL` | 비우면 `data/radar.db`. PostgreSQL/Supabase는 `postgresql+psycopg://...` (+ `pip install psycopg[binary]`) |

## 3. 실행

```bash
python -m src.main sources-check
```
모든 출처의 실제 접근 여부를 확인해 `output/sources-report.md`로 남긴다.

```bash
python -m src.main hourly
```
전체 출처 수집 → 분석 (1시간 간격용).

```bash
python -m src.main wired
```
WIRED 4개 피드 수집 → 분석 → 80점 이상 즉시 알림 (30분 간격용).

```bash
python -m src.main briefing --no-send
```
최신 수집 후 `output/briefing-YYYY-MM-DD.md` / `.html` 생성. `--no-send`를 빼면 ntfy로 발송한다.
같은 날짜는 한 번만 발송된다(`--force`로 재발송, `--skip-collect`로 DB만 사용).

```bash
python -m src.main notify-test daily
```
ntfy 테스트 알림(`daily` / `wired` / `alert`).

```bash
python -m src.main status
```
기사 수, 분석 상태, 최근 24시간 수집 실패, 최근 알림 이력.

```bash
python -m pytest -q
```

## 4. ntfy 앱 구독

1. 휴대폰에 ntfy 앱 설치(Android: Google Play/F-Droid, iOS: App Store).
2. 토픽 이름 3개를 정한다. **ntfy.sh 공개 서버의 토픽은 이름만 알면 누구나 구독·발행할 수 있다.**
   `radar-daily-7f3k9q...`처럼 추측하기 어려운 이름을 쓰거나, 계정·토큰으로 접근을 제한한다.
3. 앱에서 `+` → 토픽 이름 입력 → Subscribe. 세 토픽 모두 구독.
4. `.env`에 토픽을 넣고 `NTFY_DRY_RUN=false`로 바꾼 뒤 `python -m src.main notify-test daily`.

알림 형식

| 종류 | 제목 | 우선순위 | 클릭 |
|---|---|---|---|
| 정기 브리핑 | `AI×MEDIA 모닝 브리핑 \| YYYY-MM-DD` | 3(기본) | 브리핑 HTML(공개 주소 설정 시) |
| WIRED | `[WIRED] 한국어 제목` | 4 | 원문 |
| 속보(90+) | `[속보] 한국어 제목` | 5 | 원문 |

푸시 본문은 900자 이내로 자른다. 전문은 브리핑 파일에 있다.

## 5. GitHub Actions

`.github/workflows/radar.yml` 하나로 세 작업을 돌린다. cron은 UTC다(KST = UTC+9).

| 작업 | cron(UTC) | KST |
|---|---|---|
| 정기 브리핑 | `40 23 * * *` → 09:00까지 대기 후 실행 | 08:40 기상, 09:00 실행 |
| 브리핑 보조 | `10 0 * * *` (예약 지연·누락 대비) | 09:10 |
| WIRED | `7,37 * * * *` | 30분 간격 |
| 전체 수집 | `23 * * * *` | 1시간 간격 |

- GitHub 예약 실행은 수 분~수십 분 늦거나 누락될 수 있다. 그래서 브리핑을 두 번 예약하고, 날짜별 발송 이력으로 한 번만 보낸다.
  08:40 실행이 09:00 이전에 너무 일찍 깨면(40분 초과) 아무것도 하지 않고 09:10 실행에 맡긴다.
- 모든 작업은 `concurrency: radar-db`로 한 번에 하나만 돈다. 중복 실행돼도 기사(URL 해시)·알림(종류+키) 모두 유일 제약으로 막힌다.
- `DATABASE_URL`이 없으면 SQLite 파일을 Actions 캐시로 이어 쓴다(실행마다 새 키 저장, 접두어로 복원).

설정 순서

1. 저장소 Settings → Secrets and variables → Actions
   - **Secrets**: `ANTHROPIC_API_KEY`(또는 `OPENAI_API_KEY`), `NTFY_DAILY_TOPIC`, `NTFY_WIRED_TOPIC`, `NTFY_ALERT_TOPIC`, 필요 시 `NTFY_TOKEN`, `DATABASE_URL`
   - **Variables**: `LLM_PROVIDER=anthropic`, 실제 발송 시 `NTFY_DRY_RUN=false`, 선택 `LLM_MODEL`, `BRIEFING_PUBLIC_BASE_URL`, `PUBLISH_PAGES`
2. Actions 탭 → AI x MEDIA RADAR → Run workflow → `sources-check`, `notify-test-daily`로 먼저 확인.
3. (선택) 브리핑을 웹으로 열려면 Settings → Pages → Source: GitHub Actions, 변수 `PUBLISH_PAGES=true`,
   `BRIEFING_PUBLIC_BASE_URL=https://<계정>.github.io/<저장소>`. **공개 Pages는 누구나 볼 수 있다.** 비공개 저장소의 Pages는 유료 플랜이 필요하다.
   Pages는 매번 그날 브리핑으로 교체되므로 이전 날짜 링크는 열리지 않는다(과거본은 Actions 아티팩트에 14일 보관).

## 6. 출처 추가·삭제

`config/sources.yaml`에 항목을 넣거나 `enabled: false`로 끈다. `id`는 DB에 저장되므로 바꾸지 않는다.

```yaml
  - id: example_media
    name: 예시 매체
    group: korea            # ai_global | media_global | korea | korea_company | wired
    type: rss               # rss | arxiv_api | html_list
    url: https://example.com/rss.xml
    enabled: true
    default_category: MEDIA # AI | MEDIA | BOTH
    prefilter: true         # 기사량이 많고 주제가 섞인 피드면 true
    max_items: 30
    verified: "2026-11-09 200 OK"
```

추가 후 `python -m src.main sources-check`로 확인한다.
공식 RSS/API가 없는 사이트(`html_list`)는 이용약관과 robots.txt를 먼저 확인한다. 코드는 robots.txt가 막으면 수집하지 않는다.
날짜에 시간대가 없는 국내 피드는 KST로 해석한다(`naive_tz`로 바꿀 수 있음).

## 7. 장애 점검

| 증상 | 확인 |
|---|---|
| 특정 출처가 안 들어옴 | `python -m src.main status`의 수집 실패 목록 → `sources-check`. 403이면 봇 차단, 404면 피드 주소 변경 |
| 알림이 안 옴 | `status`의 최근 알림 상태: `dry_run`이면 `NTFY_DRY_RUN=false`, `failed`면 `last_error`(401/403은 토큰, 429는 발송 한도) |
| 같은 알림이 두 번 | 정상이라면 불가능(유일 제약). Actions 캐시가 복원되지 않아 DB가 초기화됐는지 실행 로그의 "SQLite 복원" 확인 |
| 브리핑이 영어 원문 발췌로 나옴 | `LLM_PROVIDER`/API 키 미설정 또는 LLM 오류. 로그의 "LLM 출력 검증 실패" 확인 |
| 섹션이 5개보다 적음 | 72시간으로 넓혀도 25점(WIRED 15점) 이상 기사가 부족한 경우. 억지로 채우지 않는다 |
| 예약 실행이 안 돎 | 60일간 커밋이 없는 공개 저장소는 GitHub가 예약 실행을 멈춘다. Actions 탭에서 다시 켠다 |

로그: `data/logs/radar.log`(로컬), Actions 아티팩트(원격).

## LLM 안전장치

- 출력은 Pydantic 스키마로 검증하고, 실패하면 한 번 다시 요청한 뒤 규칙 기반으로 대체한다.
- 점수는 항목별 상한(30/30/25/15)으로 자르고 합계는 코드가 계산한다.
- 출처 URL은 LLM 출력이 아니라 수집한 원문 URL을 쓴다. 텍스트 안의 다른 URL은 지운다.
- 요약에 원문에 없는 2자리 이상 수치가 있으면 신뢰도를 `low`로 낮추고 브리핑에 "확인 필요"로 표시한다.
- 요약(사실)과 분석(중요한 이유·시사점·취재 관점)을 분리해 표기한다.
- 규칙 기반 점수는 즉시 푸시에 쓰지 않는다(오탐 방지). LLM 분석 결과만 WIRED·속보 알림 대상이다.
