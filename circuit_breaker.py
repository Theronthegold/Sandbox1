"""
circuit_breaker.py

모든 모델 호출(model-client)은 반드시 이 breaker를 거쳐야 합니다.

동작 원리:
  1. 호출 직전 check_before_call() 을 호출 — 조건 위반 시 예외를 던져서
     실제 API 호출 자체가 나가지 않도록 막습니다.
  2. 호출 직후 record_call() 을 호출 — 실제 사용된 토큰/비용을 기록합니다.

이 파일 하나가 "200불 중 한 방에 다 날아가는" 최악의 시나리오를 막는
마지막 방어선입니다. 오케스트레이터나 에이전트 로직보다 먼저 완성하고,
반드시 단위 테스트로 임계값 동작을 확인한 뒤 실제 모델 호출에 연결하세요.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("circuit_breaker")


class BudgetExceededError(Exception):
    """누적 비용이 설정한 상한을 초과했을 때 발생."""


class AgentLoopSuspectedError(Exception):
    """특정 에이전트가 짧은 시간 안에 과도하게 호출됐을 때 (무한루프 의심) 발생."""


@dataclass
class CallRecord:
    agent_name: str
    timestamp: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CircuitBreaker:
    # ---- 설정값: 프로젝트 상황에 맞게 조정하세요 ----
    max_total_cost_usd: float = 150.0        # 전체 예산(200불)의 하드 스탑 라인. 여유를 두고 낮게 잡을 것.
    max_calls_per_agent_per_minute: int = 20  # 같은 에이전트가 1분 안에 이 이상 호출되면 루프로 간주
    warn_threshold_usd: float = 100.0         # 이 금액을 넘으면 경고 로그만 남김 (아직 차단은 아님)

    # ---- 내부 상태 (직접 건드리지 마세요) ----
    _records: List[CallRecord] = field(default_factory=list)
    _total_cost: float = 0.0

    def check_before_call(self, agent_name: str) -> None:
        """모델 호출 직전에 반드시 호출. 조건 위반 시 예외 발생 → 호출을 아예 내보내지 않음."""
        if self._total_cost >= self.max_total_cost_usd:
            raise BudgetExceededError(
                f"누적 비용 ${self._total_cost:.2f}가 상한 ${self.max_total_cost_usd:.2f}를 "
                f"초과했습니다. 모든 모델 호출을 중단합니다."
            )

        recent = self._recent_calls_by_agent(agent_name, window_seconds=60)
        if len(recent) >= self.max_calls_per_agent_per_minute:
            raise AgentLoopSuspectedError(
                f"'{agent_name}' 에이전트가 최근 1분간 {len(recent)}회 호출되었습니다. "
                f"무한루프가 의심되어 이 에이전트의 호출을 중단합니다."
            )

    def record_call(
        self,
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """모델 호출 직후에 반드시 호출해서 실제 사용량을 기록."""
        record = CallRecord(
            agent_name=agent_name,
            timestamp=time.time(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        self._records.append(record)
        self._total_cost += cost_usd

        if self._total_cost >= self.warn_threshold_usd:
            logger.warning(
                f"[경고] 누적 비용 ${self._total_cost:.2f} — "
                f"경고 임계값(${self.warn_threshold_usd:.2f})을 넘었습니다."
            )

        logger.info(
            f"[{agent_name}] tokens(in={input_tokens}, out={output_tokens}) "
            f"cost=${cost_usd:.4f} cumulative=${self._total_cost:.2f}"
        )

    def _recent_calls_by_agent(self, agent_name: str, window_seconds: int) -> List[CallRecord]:
        cutoff = time.time() - window_seconds
        return [
            r for r in self._records
            if r.agent_name == agent_name and r.timestamp >= cutoff
        ]

    @property
    def total_cost(self) -> float:
        return self._total_cost


# 전역 싱글톤 — 모든 에이전트/모델 클라이언트가 반드시 "이 인스턴스"를 공유해야
# 의미가 있습니다. 에이전트마다 새 CircuitBreaker()를 만들면 안 됩니다.
breaker = CircuitBreaker()


# ---- 사용 예시 (model-client에서 이런 식으로 감싸서 쓰세요) ----
#
# from lib.circuit_breaker import breaker, BudgetExceededError, AgentLoopSuspectedError
#
# async def call_model(agent_name: str, prompt: str):
#     breaker.check_before_call(agent_name)   # 여기서 예외 나면 실제 호출 안 나감
#     response = await client.messages.create(...)
#     cost = estimate_cost(response.usage)     # 모델별 단가로 직접 계산
#     breaker.record_call(
#         agent_name=agent_name,
#         input_tokens=response.usage.input_tokens,
#         output_tokens=response.usage.output_tokens,
#         cost_usd=cost,
#     )
#     return response
