"""
opus_client.py

Claude Opus 5 (claude-opus-5) 호출용 클라이언트. Fable 의 폴백으로 사용.

Opus 5 특이사항:
  - thinking={"type": "adaptive"} 를 명시 (생략해도 adaptive 지만 의도를 드러내기 위해 명시).
  - budget_tokens 는 400. effort 로만 조절.
"""

from __future__ import annotations

from typing import Any, List, Optional

from anthropic.types import Message, MessageParam

from .base import BaseModelClient
from .pricing import OPUS_5


class OpusClient(BaseModelClient):
    MODEL = OPUS_5

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
        kwargs.setdefault("thinking", {"type": "adaptive"})
        return await self._client.messages.create(**kwargs)
