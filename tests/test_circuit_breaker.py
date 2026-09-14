"""circuit_breaker 임계값 동작 검증 (stdlib unittest, 외부 의존성 없음)."""

import unittest

from lib.circuit_breaker import (
    AgentLoopSuspectedError,
    BudgetExceededError,
    CircuitBreaker,
)


class CircuitBreakerTest(unittest.TestCase):
    def test_budget_exceeded_blocks_next_call(self):
        b = CircuitBreaker(max_total_cost_usd=1.0, warn_threshold_usd=0.5)
        b.check_before_call("a")
        b.record_call("a", 10, 10, 0.6)
        b.check_before_call("a")            # 0.6 < 1.0 → 아직 허용
        b.record_call("a", 10, 10, 0.5)     # 누적 1.1
        with self.assertRaises(BudgetExceededError):
            b.check_before_call("a")

    def test_budget_exactly_at_limit_blocks(self):
        b = CircuitBreaker(max_total_cost_usd=1.0)
        b.record_call("a", 0, 0, 1.0)
        with self.assertRaises(BudgetExceededError):
            b.check_before_call("a")

    def test_loop_detection_per_agent(self):
        b = CircuitBreaker(max_calls_per_agent_per_minute=3)
        for _ in range(3):
            b.check_before_call("looper")
            b.record_call("looper", 1, 1, 0.0)
        with self.assertRaises(AgentLoopSuspectedError):
            b.check_before_call("looper")
        # 다른 에이전트는 영향 없음
        b.check_before_call("other")

    def test_zero_cost_calls_still_count_toward_loop(self):
        """실패 호출을 0 비용으로 기록해도 루프 감지에는 잡혀야 한다."""
        b = CircuitBreaker(max_calls_per_agent_per_minute=2)
        b.record_call("a", 0, 0, 0.0)
        b.record_call("a", 0, 0, 0.0)
        with self.assertRaises(AgentLoopSuspectedError):
            b.check_before_call("a")

    def test_total_cost_accumulates(self):
        b = CircuitBreaker()
        b.record_call("a", 1, 1, 0.25)
        b.record_call("b", 1, 1, 0.5)
        self.assertAlmostEqual(b.total_cost, 0.75)


if __name__ == "__main__":
    unittest.main()
