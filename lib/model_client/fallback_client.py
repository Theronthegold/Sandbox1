"""
fallback_client.py

primary 가 실패하거나 요청을 거절(stop_reason="refusal")하면 fallback 으로
같은 요청을 다시 보내는 조합 클라이언트.

에이전트 입장에서는 여전히 call() 하나만 보입니다.
breaker 는 내부의 각 클라이언트가 자기 호출을 각각 거치므로 여기서는 건드리지 않습니다.

폴백하지 않는 경우:
  - BudgetExceededError / AgentLoopSuspectedError (breaker 차단) 는 그대로 전파.
    예산이 바닥났는데 다른 모델로 우회하는 것은 breaker 의 목적에 반합니다.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from anthropic.types import MessageParam

from .base import ModelCallError, ModelClient, ModelResponse

logger = logging.getLogger("model_client")


class FallbackClient(ModelClient):
    def __init__(self, primary: ModelClient, fallback: ModelClient) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def model(self) -> str:
        return self.primary.model

    async def call(
        self,
        agent_name: str,
        messages: List[MessageParam],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        **extra: Any,
    ) -> ModelResponse:
        try:
            response = await self.primary.call(
                agent_name, messages, system=system, max_tokens=max_tokens, **extra
            )
        except ModelCallError as e:
            logger.warning(
                "[%s] %s 실패 → %s 로 폴백: %s",
                agent_name, self.primary.model, self.fallback.model, e,
            )
        else:
            if not response.refused:
                return response
            logger.warning(
                "[%s] %s 거절(refusal) → %s 로 폴백",
                agent_name, self.primary.model, self.fallback.model,
            )

        return await self.fallback.call(
            agent_name, messages, system=system, max_tokens=max_tokens, **extra
        )
