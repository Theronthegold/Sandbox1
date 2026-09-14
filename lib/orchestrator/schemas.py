"""
schemas.py

에이전트 입출력과 Job 모델. 모든 에이전트 출력은 여기 정의된 pydantic 모델의
JSON 스키마를 API 에 강제(output_config.format)하므로 파싱 실패가 구조적으로 막힙니다.

주의: 구조화 출력 스키마는 additionalProperties=false 가 필요해서 StrictModel 을 상속합니다.
Optional 필드는 anyOf 로 변환되어 스키마가 복잡해지므로 기본값(-1, "") 으로 대신합니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 주제
# ---------------------------------------------------------------------------

class TopicArea(str, Enum):
    money_psychology = "money_psychology"   # 돈의 심리학: 행동경제학 기반 소비/저축/투자 심리
    ai_tech = "ai_tech"                     # AI 와 테크 설명


TOPIC_LABELS = {
    TopicArea.money_psychology: "돈의 심리학 (행동경제학 기반 소비·저축·투자 심리)",
    TopicArea.ai_tech: "AI 와 테크 설명 (최신 AI/기술을 일반인 눈높이로)",
}


# ---------------------------------------------------------------------------
# researcher
# ---------------------------------------------------------------------------

class ResearchInput(StrictModel):
    topic_area: TopicArea
    topic_label: str
    recent_titles: List[str] = Field(default_factory=list, description="이미 만든 쇼츠 제목. 중복 회피용")
    target_seconds: int = 45


class Candidate(StrictModel):
    title_idea: str = Field(description="가제. 15자 내외")
    hook: str = Field(description="첫 2초 후킹 문장")
    angle: str = Field(description="이 주제를 다루는 차별화된 각도")
    why_now: str = Field(description="지금 이 주제가 먹히는 이유")
    sources: List[str] = Field(description="근거 URL 또는 출처명")
    risk: Literal["low", "medium", "high"] = Field(description="사실 오류/정책 위반 리스크")


class ResearchOutput(StrictModel):
    candidates: List[Candidate] = Field(min_length=3, max_length=5)
    picked_index: int = Field(description="candidates 에서 고른 인덱스 (0부터)")
    rationale: str


# ---------------------------------------------------------------------------
# writer / critic
# ---------------------------------------------------------------------------

class WriterInput(StrictModel):
    topic_label: str
    candidate: Candidate
    target_seconds: int = 45
    feedback: str = Field(default="", description="critic 의 수정 요청 (재작성 시)")
    previous_script: str = Field(default="", description="이전 대본 JSON (재작성 시)")


class ScriptScene(StrictModel):
    narration: str = Field(description="한 문장. TTS 로 그대로 읽힘")


class Script(StrictModel):
    title_working: str
    scenes: List[ScriptScene] = Field(min_length=4, max_length=12, description="첫 씬은 후킹, 마지막 씬은 CTA")


class CriticInput(StrictModel):
    topic_label: str
    candidate: Candidate
    script: Script
    target_seconds: int = 45
    estimated_seconds: float = Field(description="글자 수로 추정한 나레이션 길이")


class CriticIssue(StrictModel):
    scene_index: int = Field(description="문제 씬 인덱스. 전체 문제면 -1")
    kind: Literal["fact", "hook", "length", "clarity", "policy", "flow"]
    detail: str
    fix: str = Field(description="구체적 수정 제안")


class CriticOutput(StrictModel):
    approve: bool
    score: int = Field(ge=0, le=100)
    issues: List[CriticIssue]
    summary: str = Field(description="writer 에게 전달할 한 문단 피드백")


# ---------------------------------------------------------------------------
# director
# ---------------------------------------------------------------------------

class DirectorInput(StrictModel):
    topic_label: str
    script: Script


class DirectedScene(StrictModel):
    narration: str = Field(description="script 의 씬 나레이션 그대로")
    stock_query: str = Field(description="Pexels 검색어. 영어 2~4단어, 구체적 피사체")
    emphasis: List[str] = Field(description="나레이션 중 강조 색으로 표시할 단어 0~2개. 나레이션에 있는 단어 그대로")
    preset: Literal["pop", "calm", "big"] = "pop"


class DirectorOutput(StrictModel):
    scenes: List[DirectedScene]
    mood: Literal["calm", "upbeat", "tense", "inspiring"]


# ---------------------------------------------------------------------------
# publisher
# ---------------------------------------------------------------------------

class PublishInput(StrictModel):
    topic_label: str
    script: Script


class PublishOutput(StrictModel):
    title: str = Field(description="유튜브 제목. 40자 이내, #Shorts 포함")
    description: str
    hashtags: List[str] = Field(max_length=10)


# ---------------------------------------------------------------------------
# qa
# ---------------------------------------------------------------------------

class QAInput(StrictModel):
    scenes: List[DirectedScene]
    duration_seconds: float
    frame_count: int = Field(description="첨부된 프레임 이미지 수 (씬 순서대로)")


class QAIssue(StrictModel):
    severity: Literal["block", "warn"]
    scene_index: int = -1
    detail: str


class QAOutput(StrictModel):
    passed: bool
    issues: List[QAIssue]


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RESEARCHING = "RESEARCHING"
    SCRIPTING = "SCRIPTING"
    REVIEWING = "REVIEWING"
    DIRECTING = "DIRECTING"
    PRODUCING = "PRODUCING"
    QA = "QA"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


TERMINAL = {JobStatus.PUBLISHED, JobStatus.FAILED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    topic_area: TopicArea
    status: JobStatus = JobStatus.QUEUED
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    cost_usd: float = 0.0
    review_rounds: int = 0
    error: str = ""
    warnings: List[str] = Field(default_factory=list)
    # 단계별 산출물. 키: research, script, critic, directed, produce, publish_meta, qa, video_id
    artifacts: Dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def topic_label(self) -> str:
        return TOPIC_LABELS[self.topic_area]


def estimate_narration_seconds(script: Script, chars_per_sec: float = 5.5) -> float:
    """한국어 TTS 평균 속도로 대본 길이 추정. 씬 사이 자연스러운 쉼 0.3초 포함."""
    chars = sum(len(s.narration) for s in script.scenes)
    return chars / chars_per_sec + 0.3 * len(script.scenes)
