# Shorts Orchestrator

유튜브 쇼츠(실사 스톡 + 텍스트 애니메이션 + 한국어 나레이션)를 자동 생성하는 멀티 에이전트 오케스트레이션과 관리 대시보드.

주제 두 가지: **돈의 심리학**(행동경제학 기반 소비·저축·투자 심리), **AI 와 테크 설명**.

## 구조

```
lib/
  circuit_breaker.py     모든 모델 호출의 예산 상한 / 에이전트 루프 차단 (마지막 방어선)
  model_client/          Fable 5.1 / Opus 5 클라이언트 + 폴백, 단가표, breaker 강제
  tools/                 LLM 이 못 하는 일을 무료 도구로: edge-tts, Pexels, ASS 자막, ffmpeg, YouTube
  orchestrator/          상태 머신(pipeline), 에이전트 6개, producer, SQLite store, demo 클라이언트
  dashboard/             FastAPI + SSE 대시보드
prompts/                 에이전트별 시스템 프롬프트 (.md)
scripts/run_job.py       CLI
scripts/serve.py         대시보드 서버
tests/                   unittest (외부 API 없이 전부 실행 가능)
```

파이프라인: `QUEUED → RESEARCHING → SCRIPTING ⇄ REVIEWING → DIRECTING → PRODUCING → QA → AWAITING_APPROVAL → PUBLISHING → PUBLISHED`

| 에이전트 | 역할 | 모델 |
|---|---|---|
| researcher | 웹 검색으로 주제 후보 3~5개 조사, 1개 선택 | Fable 5.1, medium |
| writer | 문장 단위 씬 대본 | Fable 5.1, high |
| critic | 사실/후킹/길이/정책 검수, 재작성 요청 (최대 2회) | Fable 5.1, medium |
| director | 씬별 스톡 검색어, 강조 단어, 자막 프리셋, 음악 mood | Fable 5.1, medium |
| publisher | 제목/설명/해시태그 | Fable 5.1, low |
| qa | 렌더된 프레임을 비전으로 검수 | Fable 5.1, medium |

TTS, 스톡 다운로드, 자막, 렌더, 업로드는 에이전트가 아니라 `producer` 가 도구로 직접 실행합니다.
Fable 이 실패하거나 거절(refusal)하면 Opus 5 로 자동 폴백합니다.

## 설치

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

`.env` (커밋되지 않음):

```
ANTHROPIC_API_KEY=sk-ant-...      # https://console.anthropic.com/settings/keys
PEXELS_API_KEY=...                # https://www.pexels.com/api/  (없으면 단색 placeholder 클립)
MODEL_CLIENT=fallback             # fallback | fable | opus
YOUTUBE_CLIENT_SECRET=secrets/client_secret.json   # 업로드 시
```

배경음악: https://pixabay.com/music/ 에서 받아 `assets/music/calm_*.mp3`, `upbeat_*.mp3` 등으로 저장. 없으면 음악 없이 렌더.

## 실행

```
# 대시보드 (권장) → http://127.0.0.1:8000
.venv\Scripts\python.exe -m scripts.serve

# CLI
.venv\Scripts\python.exe -m scripts.run_job new --topic money_psychology
.venv\Scripts\python.exe -m scripts.run_job show <job_id>
.venv\Scripts\python.exe -m scripts.run_job approve <job_id>

# API 키 없이 데모 (에이전트 응답은 준비된 것, TTS/렌더는 실제)
.venv\Scripts\python.exe -m scripts.run_job --demo new

# 스모크 테스트: 가짜 작업 3개 → 큐 → echo 워커 → 모델 → breaker → DB → 대시보드 /tasks
.venv\Scripts\python.exe -m scripts.smoke_test          # 실제 모델, 1센트 안팎
.venv\Scripts\python.exe -m scripts.smoke_test --demo   # 키 없이
```

범용 워커 경로(`lib/orchestrator/queue.py`, `worker.py`, `agents/echo_agent.py`)는 쇼츠 파이프라인과 독립적이며,
새 워커 에이전트는 `Agent` 를 상속해 `name` / `input_model` / `output_model` 과 `prompts/<name>.md` 만 정의하면 됩니다.

대시보드의 "데모 (키 없이)" 체크박스도 같은 동작입니다.

## 안전장치

- 전역 breaker: 누적 150달러에서 하드 스탑, 100달러에서 경고, 에이전트당 분당 20회 초과 시 루프로 간주해 차단.
- 편당 예산 1.5달러 (`--budget`). 초과 시 그 Job 만 FAILED.
- 실패한 모델 호출도 0비용으로 기록되어 재시도 폭주가 루프 감지에 잡힘.
- 업로드 전 사람 승인 게이트, 업로드는 항상 private.

## 테스트

```
.venv\Scripts\python.exe -m unittest discover -s tests
```
