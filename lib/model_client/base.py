"""
base.py

모든 모델 클라이언트의 공통 인터페이스.

계층:
  ModelClient        - 에이전트가 의존하는 추상 인터페이스. call() 하나만 있음.
  BaseModelClient    - 실제 SDK를 호출하는 클라이언트의 공통 구현(템플릿 메서드).
                       call() 안에서 breaker.check_before_call() / record_call() 을
                       강제하고, 서브클래스는 _create() 만 구현합니다.
                       그래서 서브클래스가 breaker 를 우회할 방법이 없습니다.

에이전트 코드는 ModelClient 타입만 알면 되고, 실제 모델(Fable/Opus/폴백 조합)은
lib.model_client.get_client() 에서 결정합니다.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, List, Optional

import anthropic
from anthropic.types import Message, MessageParam

from lib.circuit_breaker import CircuitBreaker, breaker as default_breaker

from .pricing import estimate_cost

logger = logging.getLogger("model_client")


# ---------------------------------------------------------------------------
# 응답 / 예외 타입
# ---------------------------------------------------------------------------

@dataclass
class ModelResponse:
    """에이전트가 보는 응답. SDK 타입에 의존하지 않도록 필요한 것만 뽑아 둡니다."""

    text: str                 # text 블록들을 이어붙인 결과
    model: str                # 실제로 응답한 모델 ID
    stop_reason: str          # "end_turn" | "max_tokens" | "tool_use" | "refusal" | ...
    input_tokens: int
    output_tokens: int
    cost_usd: float           # 이 호출 한 번의 비용 (breaker 에 기록된 값과 동일)
    raw: Message              # tool_use 블록 등 원본이 필요할 때만 사용

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class ModelCallError(Exception):
    """API 호출 자체가 실패했을 때. 원인 예외는 __cause__ 로 확인."""

    def __init__(self, model: str, agent_name: str, cause: Exception) -> None:
        self.model = model
        self.agent_name = agent_name
        super().__init__(f"[{agent_name}] {model} 호출 실패: {type(cause).__name__}: {cause}")


# ---------------------------------------------------------------------------
# 인터페이스
# ---------------------------------------------------------------------------

class ModelClient(ABC):
    """에이전트가 의존하는 유일한 인터페이스."""

    @property
    @abstractmethod
    def model(self) -> str:
        """이 클라이언트가 1차로 사용하는 모델 ID."""

    @abstractmethod
    async def call(
        self,
        agent_name: str,
        messages: List[MessageParam],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        **extra: Any,
    ) -> ModelResponse:
        """모델을 한 번 호출합니다.

        agent_name: breaker 가 에이전트별 호출 빈도를 추적하는 키.
        extra:      tools 등 SDK 파라미터를 그대로 넘깁니다.
        """


# ---------------------------------------------------------------------------
# SDK 호출 클라이언트 공통 구현
# ---------------------------------------------------------------------------

class BaseModelClient(ModelClient):
    """Anthropic SDK 를 직접 호출하는 클라이언트의 공통 구현.

    서브클래스는 MODEL 과 _create() 만 정의합니다. call() 은 오버라이드하지 마세요.
    """

    MODEL: ClassVar[str]  # 서브클래스에서 지정

    def __init__(
        self,
        *,
        breaker: CircuitBreaker = default_breaker,
        api_key: Optional[str] = None,
        max_tokens: int = 16000,
        effort: str = "high",       # low | medium | high | xhigh | max
        timeout: float = 600.0,     # seconds
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다.")

        self.breaker = breaker
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout)

    @property
    def model(self) -> str:
        return self.MODEL

    # ---- 에이전트 진입점: breaker 를 반드시 거침 ----

    async def call(
        self,
        agent_name: str,
        messages: List[MessageParam],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        **extra: Any,
    ) -> ModelResponse:
        # 1) 호출 전 검사. 예외가 나면 API 호출 자체가 나가지 않음.
        #    (BudgetExceededError / AgentLoopSuspectedError 는 그대로 전파)
        self.breaker.check_before_call(agent_name)

        # 2) 실제 호출
        try:
            message = await self._create(
                messages=messages,
                system=system,
                max_tokens=max_tokens or self.max_tokens,
                **extra,
            )
        except anthropic.APIError as e:
            # 실패한 호출도 0 비용으로 기록해서 재시도 폭주가 루프 감지에 잡히게 함
            self.breaker.record_call(agent_name, 0, 0, 0.0)
            logger.warning("[%s] %s 호출 실패: %s", agent_name, self.model, e)
            raise ModelCallError(self.model, agent_name, e) from e

        # 3) 비용 계산 + 기록
        usage = message.usage
        cost = estimate_cost(
            self.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
        )
        self.breaker.record_call(
            agent_name=agent_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )

        if message.stop_reason == "refusal":
            logger.warning("[%s] %s 가 요청을 거절했습니다 (stop_reason=refusal).", agent_name, self.model)

        return ModelResponse(
            text=_join_text(message.content),
            model=message.model,
            stop_reason=message.stop_reason or "",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
            raw=message,
        )

    # ---- 서브클래스가 구현하는 부분 ----

    @abstractmethod
    async def _create(
        self,
        *,
        messages: List[MessageParam],
        system: Optional[str],
        max_tokens: int,
        **extra: Any,
    ) -> Message:
        """SDK 호출만 담당. breaker 나 비용 계산은 절대 여기서 하지 않습니다."""

    def _base_kwargs(
        self,
        *,
        messages: List[MessageParam],
        system: Optional[str],
        max_tokens: int,
        **extra: Any,
    ) -> dict:
        """서브클래스가 messages.create() 에 넘길 공통 인자."""
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": {"effort": self.effort},
            **extra,
        }
        if system is not None:
            kwargs["system"] = system
        return kwargs


def _join_text(blocks: Iterable[Any]) -> str:
    return "".join(b.text for b in blocks if getattr(b, "type", None) == "text")
