"""
lib.model_client

에이전트 코드에서는 이렇게만 쓰면 됩니다:

    from lib.model_client import get_client

    client = get_client()                       # 환경변수 MODEL_CLIENT 로 선택
    resp = await client.call("planner", [{"role": "user", "content": "..."}])
    print(resp.text, resp.cost_usd)

어떤 모델을 쓰는지는 MODEL_CLIENT 환경변수 하나로 바뀝니다:
    fallback  (기본) Fable 5.1 → 실패/거절 시 Opus 5
    fable            Fable 5.1 만
    opus             Opus 5 만
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import BaseModelClient, ModelCallError, ModelClient, ModelResponse
from .fable_client import FableClient
from .fallback_client import FallbackClient
from .opus_client import OpusClient
from .pricing import FABLE_5_1, MODEL_PRICING, OPUS_5, ModelPricing, estimate_cost

__all__ = [
    "get_client",
    "ModelClient",
    "BaseModelClient",
    "ModelResponse",
    "ModelCallError",
    "FableClient",
    "OpusClient",
    "FallbackClient",
    "ModelPricing",
    "MODEL_PRICING",
    "estimate_cost",
    "FABLE_5_1",
    "OPUS_5",
]

DEFAULT_CLIENT = "fallback"


def get_client(name: Optional[str] = None, **kwargs: Any) -> ModelClient:
    """이름으로 클라이언트를 만듭니다.

    name: "fallback" | "fable" | "opus". None 이면 환경변수 MODEL_CLIENT,
          그것도 없으면 "fallback".
    kwargs: BaseModelClient 생성자 인자 (breaker, api_key, max_tokens, effort, timeout).
            fallback 조합이면 두 클라이언트 모두에 같은 값이 전달됩니다.
    """
    name = (name or os.environ.get("MODEL_CLIENT") or DEFAULT_CLIENT).lower()

    if name == "fable":
        return FableClient(**kwargs)
    if name == "opus":
        return OpusClient(**kwargs)
    if name == "fallback":
        return FallbackClient(primary=FableClient(**kwargs), fallback=OpusClient(**kwargs))

    raise ValueError(f"알 수 없는 MODEL_CLIENT '{name}'. fallback | fable | opus 중 하나여야 합니다.")
