"""
pricing.py

모델별 단가 상수. 비용 계산에 쓰이는 숫자는 전부 이 파일에만 둡니다.
단가가 바뀌면 MODEL_PRICING 만 고치면 됩니다.

단위: USD / 1M tokens (Anthropic 1st-party API 기준, 2026-09 시점).
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float
    output_per_mtok: float
    cache_write_per_mtok: float  # 프롬프트 캐시 쓰기 (보통 input × 1.25)
    cache_read_per_mtok: float   # 프롬프트 캐시 읽기 (보통 input × 0.1)


# ---- 모델 ID ----
FABLE_5_1 = "claude-fable-5-1"
OPUS_5 = "claude-opus-5"


# ---- 단가표: 여기만 고치면 됩니다 ----
MODEL_PRICING: Dict[str, ModelPricing] = {
    FABLE_5_1: ModelPricing(
        input_per_mtok=10.0,
        output_per_mtok=50.0,
        cache_write_per_mtok=12.5,
        cache_read_per_mtok=1.0,
    ),
    OPUS_5: ModelPricing(
        input_per_mtok=5.0,
        output_per_mtok=25.0,
        cache_write_per_mtok=6.25,
        cache_read_per_mtok=0.5,
    ),
}

_MTOK = 1_000_000


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """response.usage 값과 모델 단가를 곱해 USD 비용을 계산합니다.

    단가표에 없는 모델이면 KeyError 를 내서 "모르는 모델을 공짜로 계산"하는
    사고를 막습니다.
    """
    try:
        p = MODEL_PRICING[model]
    except KeyError:
        raise KeyError(
            f"'{model}' 의 단가가 MODEL_PRICING 에 없습니다. pricing.py 에 추가하세요."
        ) from None

    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_write_tokens * p.cache_write_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
    ) / _MTOK
