# Shorts Orchestrator 사용 매뉴얼

유튜브 쇼츠(실사 스톡 영상 + 텍스트 애니메이션 자막 + 한국어 나레이션)를 자동으로 기획·제작·검수하고, 사람이 승인하면 업로드하는 시스템입니다.
주제는 **돈의 심리학**과 **AI 와 테크 설명** 두 가지이고, 지정하지 않으면 번갈아 만듭니다.

한 편의 흐름:

```
조사 → 대본 → 검수(최대 2회 재작성) → 연출 → 제작(TTS·스톡·자막·렌더) → QA → [사람 승인] → 업로드(private)
```

---

## 1. 준비물

### 1-1. 프로그램

| 항목 | 확인 방법 | 설치 |
|---|---|---|
| Python 3.11 이상 | `py --version` | https://python.org |
| ffmpeg (libass 포함) | `ffmpeg -version` | `winget install Gyan.FFmpeg` (설치 후 새 터미널) |
| 프로젝트 의존성 | 아래 명령 | `py -3.14 -m venv .venv` 후 `.venv\Scripts\python.exe -m pip install -r requirements.txt` |

### 1-2. 키 (`.env` 파일)

프로젝트 루트에 `.env` 파일을 만들고 아래처럼 적습니다. 이 파일은 gitignore 되어 커밋되지 않습니다.

```
ANTHROPIC_API_KEY=sk-ant-...
PEXELS_API_KEY=...
MODEL_CLIENT=fallback
YOUTUBE_CLIENT_SECRET=secrets/client_secret.json
```

| 키 | 용도 | 없으면 | 발급 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 에이전트 6개 (조사·대본·검수·연출·메타·QA) | 데모 모드만 가능 | https://console.anthropic.com/settings/keys (크레딧 충전 필요: https://console.anthropic.com/settings/billing) |
| `PEXELS_API_KEY` | 세로 실사 스톡 영상 | 단색 placeholder 클립으로 렌더 | https://www.pexels.com/api/ (무료) |
| `YOUTUBE_CLIENT_SECRET` | 업로드 | 승인 대기까지만 진행 | Google Cloud Console → YouTube Data API v3 사용 설정 → OAuth 클라이언트(데스크톱) → JSON 다운로드 |
| `MODEL_CLIENT` | 모델 선택 | `fallback` | `fallback`(Fable→실패 시 Opus) · `fable` · `opus` |

`ANTHROPIC_API_KEY` 대신 Anthropic CLI 로 `ant auth login` 을 해 두면 키 없이도 동작합니다 (Console 계정과 크레딧은 동일하게 필요).

### 1-3. 배경음악 (선택)

https://pixabay.com/music/ 에서 곡을 받아 `assets/music/` 에 넣습니다. 파일명 앞에 분위기 태그를 붙이면 연출 에이전트가 고른 mood 에 맞춰 선택됩니다.

```
assets/music/calm_morning-light.mp3
assets/music/upbeat_city-run.mp3
assets/music/tense_deep-focus.mp3
assets/music/inspiring_new-day.mp3
```

없으면 음악 없이 렌더합니다.

---

## 2. 첫 실행

### 2-1. 키 없이 데모로 확인

```
.venv\Scripts\python.exe -m scripts.serve
```

브라우저에서 http://127.0.0.1:8000 을 열고, 상단의 **데모 (키 없이)** 가 체크된 상태로 **새 Job** 을 누릅니다.
에이전트 응답은 준비된 예시를 쓰지만 TTS·자막·렌더는 실제로 돌아가므로 40초 안팎의 영상이 만들어지고 **승인 대기** 에서 멈춥니다. 1분 정도 걸립니다.

### 2-2. 키가 오면 가장 먼저: 스모크 테스트 (1센트 안팎)

쇼츠 파이프라인을 돌리기 전에, 실제 모델 호출 경로가 끊기지 않는지 아주 작은 작업으로 확인합니다.
가짜 작업 3개(문장 한 줄 요약)를 큐에 넣고 echo 워커가 처리하며, 모든 호출은 circuit_breaker 를 거칩니다.

```
.venv\Scripts\python.exe -m scripts.smoke_test            # 실제 모델
.venv\Scripts\python.exe -m scripts.smoke_test --demo     # 키 없이 경로만
```

터미널에 작업 3건의 결과와 비용, breaker 누적이 찍히고 `output/orchestrator.db` 의 `tasks` 테이블에 기록됩니다.
대시보드(`scripts.serve`)의 상단 "워커 작업" 칩을 누르거나 http://127.0.0.1:8000/tasks 를 열면 같은 내용이 표로 보입니다.
3건이 모두 DONE 이고 비용이 0 이 아니면 키·모델·breaker·DB·대시보드가 전부 연결된 것입니다.

### 2-3. 실제 실행

`.env` 에 키를 넣은 뒤 서버를 다시 띄우고, **데모** 체크를 해제하고 **새 Job** 을 누릅니다.
첫 실제 실행에서는 아래 두 가지가 정상 동작하는지 이벤트 로그로 확인하세요.

- 리서처가 웹 검색을 실제로 수행했는지 (후보에 출처 URL 이 있는지)
- 각 에이전트 출력이 파싱 오류 없이 넘어갔는지 (로그에 "출력 파싱 실패, 재시도" 가 반복되지 않는지)

한 편당 모델 비용은 보통 0.4~0.8달러입니다 (Fable 5.1 기준, 리뷰 1회).

---

## 3. 대시보드

```
.venv\Scripts\python.exe -m scripts.serve            # http://127.0.0.1:8000
.venv\Scripts\python.exe -m scripts.serve --port 8080 --budget 2.0
```

### 화면 구성

| 영역 | 내용 |
|---|---|
| 상단 바 | 전체 누적 비용, 편당 예산, Job 수, 키 유무. 오른쪽에서 주제 선택 + 데모 여부 + 새 Job |
| 왼쪽 목록 | Job 목록. 파란 점은 실행 중, 상태 배지 색: 파랑 진행 / 노랑 승인 대기 / 초록 완료 / 빨강 실패 |
| 스테퍼 | 9단계 진행. 현재 단계가 색으로 표시됨 |
| 에이전트 카드 | 8개(리서처·작가·검수자·연출·퍼블리셔·프로듀서·QA·업로더). 상태, 누적 비용, 마지막 메시지 |
| 왼쪽 열 | 선정된 주제, 대본과 씬별 연출(스톡 검색어·자막 프리셋·강조 단어), 업로드 메타 |
| 오른쪽 열 | 렌더된 영상 플레이어, QA 프레임, QA 결과, 검수 결과, 이벤트 로그(실시간) |

### 버튼

| 버튼 | 나타나는 때 | 동작 |
|---|---|---|
| 승인 → 업로드 (private) | 승인 대기 | YouTube 에 비공개로 업로드. 공개 전환은 YouTube 스튜디오에서 직접 |
| 거절 | 승인 대기 | 사유를 적고 Job 을 실패 처리 |
| 실패 지점부터 재시도 | 실패 | 남아 있는 산출물을 보고 실패한 단계부터 다시 실행 |

영상은 **승인 전에 반드시 재생해 보세요.** QA 에이전트는 씬당 프레임 1장만 보므로 자막 타이밍이나 음성 어색함은 사람이 확인해야 합니다.

### 상태 의미

| 상태 | 뜻 |
|---|---|
| QUEUED | 생성됨 |
| RESEARCHING | 리서처가 주제 조사 중 |
| SCRIPTING / REVIEWING | 작가가 쓰고 검수자가 보는 중. 미승인이면 피드백을 넣어 재작성 (최대 2회) |
| DIRECTING | 연출 + 제목·설명 작성 |
| PRODUCING | TTS → 스톡 → 자막 → 음악 → 렌더 (모델 호출 없음) |
| QA | QA 에이전트가 프레임 검수 |
| AWAITING_APPROVAL | 사람 승인 대기 |
| PUBLISHING / PUBLISHED | 업로드 중 / 완료 |
| FAILED | 어느 단계든 실패. 카드 상단에 원인 표시 |

---

## 4. CLI

대시보드 없이 터미널에서도 같은 일을 할 수 있습니다.

```
.venv\Scripts\python.exe -m scripts.run_job new --topic money_psychology   # 새 Job, 승인 대기까지
.venv\Scripts\python.exe -m scripts.run_job new                            # 주제 번갈아
.venv\Scripts\python.exe -m scripts.run_job --demo new                     # 키 없이 데모
.venv\Scripts\python.exe -m scripts.run_job list
.venv\Scripts\python.exe -m scripts.run_job show <job_id>                  # 단계·이벤트 타임라인
.venv\Scripts\python.exe -m scripts.run_job run <job_id>                   # 실패 지점부터 재개
.venv\Scripts\python.exe -m scripts.run_job approve <job_id>               # 승인 → 업로드
.venv\Scripts\python.exe -m scripts.run_job reject <job_id> "사유"
```

공통 옵션: `--budget 1.5` (편당 예산 USD), `--db output/orchestrator.db`, `--auto-publish` (승인 없이 업로드, 비권장).

---

## 5. 산출물 위치

```
output/
  orchestrator.db              Job·단계·이벤트 기록 (SQLite)
  jobs/<job_id>/
    tts/scene_00.mp3 ...       씬별 나레이션
    clips/                     placeholder 클립 (Pexels 사용 시엔 assets/stock/ 에 캐시)
    subs.ass                   자막 (텍스트 편집 가능)
    short.mp4                  최종 영상 1080x1920
    frames/scene_00.jpg ...    QA 프레임
assets/stock/pexels_<id>.mp4   다운로드한 스톡 캐시 (재사용)
```

---

## 6. 비용과 안전장치

| 장치 | 기본값 | 위치 |
|---|---|---|
| 전체 하드 스탑 | 누적 150달러 | `lib/circuit_breaker.py` `max_total_cost_usd` |
| 경고 | 누적 100달러 | 같은 파일 `warn_threshold_usd` |
| 루프 차단 | 에이전트당 분당 20회 | 같은 파일 `max_calls_per_agent_per_minute` |
| 편당 예산 | 1.5달러 | `--budget` 또는 `Pipeline(per_job_budget_usd=)` |
| 리뷰 루프 상한 | 2회 | `Pipeline(max_review_rounds=)` |
| 모델 단가 | Fable 10/50, Opus 5/25 (USD per 1M tokens) | `lib/model_client/pricing.py` |

모든 모델 호출은 `check_before_call → 호출 → record_call` 을 강제로 거치며, 실패한 호출도 0비용으로 기록되어 재시도 폭주가 루프 감지에 잡힙니다. 전체 하드 스탑은 프로세스 메모리 기준이므로 서버를 재시작하면 0 에서 다시 셉니다. 대시보드 상단의 "총 비용" 은 DB 에 기록된 Job 비용 합계라 재시작과 무관합니다.

---

## 7. 바꾸고 싶을 때

| 바꾸고 싶은 것 | 위치 |
|---|---|
| 에이전트 말투·규칙 | `prompts/<agent>.md` (코드 수정 없이 편집) |
| 나레이션 목소리 | `lib/tools/tts.py` `DEFAULT_VOICE` (남 InJoon / 남 Hyunsu / 여 SunHi) |
| 자막 크기·색·위치 | `lib/tools/subtitles.py` `STYLE_PRESETS` (pop / calm / big) |
| 목표 길이 | `Pipeline(target_seconds=45)` |
| 주제 추가 | `lib/orchestrator/schemas.py` `TopicArea` + `TOPIC_LABELS`, 그리고 `prompts/researcher.md` 지침 |
| 배경음악 볼륨 | `lib/tools/render.py` `music_volume=0.12` |
| 업로드 카테고리·공개 범위 | `lib/tools/youtube.py` `category_id`, `privacy_status` |

---

## 8. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| "Anthropic 자격증명이 없습니다" | `.env` 에 `ANTHROPIC_API_KEY` 가 없음. 서버를 프로젝트 루트에서 실행했는지도 확인 |
| "ffmpeg 을 찾을 수 없습니다" | 설치 후 새 터미널을 열지 않았거나 PATH 미반영. `FFMPEG_DIR` 환경변수에 bin 폴더 경로를 넣어도 됨 |
| TTS 가 실패함 | edge-tts 는 비공식 서비스라 간헐적으로 막힘. 잠시 후 재시도. 계속되면 `lib/tools/tts.py` 를 로컬 TTS 로 교체 |
| Pexels 429 | 시간당 200회 제한. 잠시 대기. 캐시된 클립은 다시 받지 않음 |
| 리서처가 400 오류 | 웹 검색 도구 타입 미지원. 자동으로 기본 타입으로 재시도함. 그래도 실패하면 `lib/orchestrator/agents/__init__.py` `WEB_SEARCH_TOOL` 의 type 을 `web_search_20250305` 로 변경 |
| "출력 파싱 2회 실패" | 모델이 스키마와 다른 JSON 을 냄. 해당 프롬프트에 출력 예시를 보강 |
| "편당 예산 초과" | 리뷰 루프가 길었거나 대본이 김. `--budget` 을 올리거나 재시도 |
| QA 불통과로 FAILED | 카드 상단의 사유 확인. 스톡 검색어를 바꾸려면 `prompts/director.md` 조정 후 재시도 (연출 단계부터 다시 돎) |
| 자막 한글이 네모로 나옴 | 폰트 없음. `STYLE_PRESETS` 의 `font` 를 설치된 한글 폰트명으로 |
| 대시보드가 안 열림 | 8000 포트 사용 중. `--port 8080` |

테스트로 전체 상태 확인: `.venv\Scripts\python.exe -m unittest discover -s tests` (외부 API 없이 59개 실행)

---

## 9. 임시 PC 에서 흔적 지우기

1. `gh auth logout` 으로 본인 GitHub 계정 로그아웃 (원래 있던 계정은 그대로)
2. Anthropic Console 에서 오늘 만든 API 키 폐기
3. 프로젝트 폴더 삭제 (`.env`, `.venv`, `secrets/`, `output/` 포함)
4. `%LOCALAPPDATA%\pip\cache` 삭제, `winget uninstall Gyan.FFmpeg`
5. Claude Code 세션 기록: `%USERPROFILE%\.claude\projects\` 아래 이 프로젝트 폴더 삭제
6. 브라우저에서 GitHub·Console 로그인 쿠키 삭제
