"""
fable_client.py

Claude Fable 5.1 (claude-fable-5-1) 호출용 클라이언트.

Fable 5.1 특이사항:
  - thinking 은 항상 켜져 있음. thinking 파라미터를 보내지 않는다.
    ({"type": "disabled"} 나 budget_tokens 를 보내면 400)
  - 추론 깊이는 output_config.effort 로만 조절.
  - 안전 분류기가 요청을 거절하면 HTTP 200 + stop_reason="refusal" 로 온다.
    폴백 판단은 FallbackClient 에서 처리한다.
"""

from __future__ import annotations

from typing import Any, List, Optional

from anthropic.types import Message, MessageParam

from .base import BaseModelClient
from .pricing import FABLE_5_1


class FableClient(BaseModelClient):
    MODEL = FABLE_5_1

    async def _create(
        self,
        *,
        messages: List[MessageParam],
        system: Optional[str],
        max_tokens: int,
        **extra: Any,
    ) -> Message:
        kwargs = self._base_kwargs(
            messages=messages, system=system, max_tokens=max_tokens, **extra
        )
        return await self._client.messages.create(**kwargs)
