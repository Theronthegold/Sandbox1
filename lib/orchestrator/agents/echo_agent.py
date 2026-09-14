"""
echo_agent.py

검증용 최소 워커. 입력 텍스트를 모델에게 "한 줄 요약" 시키고 그대로 돌려줍니다.
쓸모보다 "큐 → 에이전트 → 모델 호출 → circuit_breaker 통과 → 결과 반환" 경로가
끊기지 않고 도는지 확인하는 게 목적입니다. 실제 키로 돌려도 한 건에 1센트 미만입니다.
"""

from __future__ import annotations

from typing import ClassVar, Type

from pydantic import BaseModel, Field

from lib.orchestrator.schemas import StrictModel

from .base_agent import Agent


class EchoInput(StrictModel):
    text: str = Field(description="요약할 원문")


class EchoOutput(StrictModel):
    summary: str = Field(description="원문을 한 문장으로 요약한 한국어 한 줄")
    char_count: int = Field(description="원문 글자 수")


class EchoAgent(Agent[EchoOutput]):
    name = "echo"
    input_model: ClassVar[Type[BaseModel]] = EchoInput
    output_model = EchoOutput
    effort = "low"
    max_tokens = 300
