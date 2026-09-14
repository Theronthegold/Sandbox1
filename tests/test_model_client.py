"""model_client 검증. SDK 호출은 mock 하고, breaker 경유/비용 계산/폴백만 확인."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx

from lib.circuit_breaker import BudgetExceededError, CircuitBreaker
from lib.model_client import (
    FABLE_5_1,
    OPUS_5,
    FableClient,
    FallbackClient,
    ModelCallError,
    OpusClient,
    estimate_cost,
    get_client,
)


def fake_message(model: str, text: str = "hi", stop_reason: str = "end_turn",
                 input_tokens: int = 1000, output_tokens: int = 500):
    """base.call() 이 읽는 필드만 갖춘 가짜 Message."""
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def api_error(status: int = 500) -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req)
    return anthropic.APIStatusError("boom", response=resp, body=None)


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
        self.breaker = CircuitBreaker(max_total_cost_usd=10.0, warn_threshold_usd=5.0)

    def make(self, cls, message=None, error=None):
        c = cls(breaker=self.breaker)
        mock = AsyncMock()
        if error is not None:
            mock.side_effect = error
        else:
            mock.return_value = message or fake_message(cls.MODEL)
        c._client.messages.create = mock
        return c, mock


class PricingTest(unittest.TestCase):
    def test_fable_cost(self):
        # 1000 in × $10/M + 500 out × $50/M = 0.01 + 0.025
        self.assertAlmostEqual(estimate_cost(FABLE_5_1, 1000, 500), 0.035)

    def test_opus_cost(self):
        self.assertAlmostEqual(estimate_cost(OPUS_5, 1000, 500), 0.0175)

    def test_cache_tokens_added(self):
        c = estimate_cost(FABLE_5_1, 0, 0, cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
        self.assertAlmostEqual(c, 12.5 + 1.0)

    def test_unknown_model_raises(self):
        with self.assertRaises(KeyError):
            estimate_cost("claude-unknown", 1, 1)


class BaseClientTest(_Base):
    async def test_success_records_cost_in_breaker(self):
        client, mock = self.make(FableClient)
        resp = await client.call("planner", [{"role": "user", "content": "x"}], system="sys")

        self.assertEqual(resp.text, "hi")
        self.assertEqual(resp.model, FABLE_5_1)
        self.assertAlmostEqual(resp.cost_usd, 0.035)
        self.assertAlmostEqual(self.breaker.total_cost, 0.035)
        self.assertFalse(resp.refused)

        kwargs = mock.call_args.kwargs
        self.assertEqual(kwargs["model"], FABLE_5_1)
        self.assertEqual(kwargs["system"], "sys")
        self.assertEqual(kwargs["output_config"], {"effort": "high"})
        self.assertNotIn("thinking", kwargs)          # Fable: thinking 파라미터 없음

    async def test_opus_sends_adaptive_thinking(self):
        client, mock = self.make(OpusClient)
        await client.call("a", [{"role": "user", "content": "x"}])
        self.assertEqual(mock.call_args.kwargs["thinking"], {"type": "adaptive"})
        self.assertNotIn("system", mock.call_args.kwargs)  # system=None 이면 안 보냄

    async def test_breaker_blocks_before_api_call(self):
        self.breaker.record_call("x", 0, 0, 10.0)        # 예산 소진
        client, mock = self.make(FableClient)
        with self.assertRaises(BudgetExceededError):
            await client.call("a", [{"role": "user", "content": "x"}])
        mock.assert_not_called()

    async def test_api_error_recorded_as_zero_cost_and_wrapped(self):
        client, mock = self.make(FableClient, error=api_error(500))
        with self.assertRaises(ModelCallError) as ctx:
            await client.call("a", [{"role": "user", "content": "x"}])
        self.assertIsInstance(ctx.exception.__cause__, anthropic.APIStatusError)
        self.assertEqual(len(self.breaker._records), 1)
        self.assertEqual(self.breaker.total_cost, 0.0)

    async def test_max_tokens_override(self):
        client, mock = self.make(FableClient)
        await client.call("a", [{"role": "user", "content": "x"}], max_tokens=42)
        self.assertEqual(mock.call_args.kwargs["max_tokens"], 42)

    def test_missing_api_key_raises(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                FableClient(breaker=self.breaker)
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved


class FallbackClientTest(_Base):
    async def test_primary_success_no_fallback(self):
        fable, fmock = self.make(FableClient)
        opus, omock = self.make(OpusClient)
        resp = await FallbackClient(fable, opus).call("a", [{"role": "user", "content": "x"}])
        self.assertEqual(resp.model, FABLE_5_1)
        omock.assert_not_called()

    async def test_refusal_falls_back_to_opus(self):
        fable, _ = self.make(FableClient, message=fake_message(FABLE_5_1, stop_reason="refusal"))
        opus, omock = self.make(OpusClient)
        resp = await FallbackClient(fable, opus).call("a", [{"role": "user", "content": "x"}])
        self.assertEqual(resp.model, OPUS_5)
        omock.assert_called_once()
        # 두 호출 모두 breaker 에 기록됨 (Fable 거절 응답도 토큰은 소모)
        self.assertEqual(len(self.breaker._records), 2)

    async def test_api_error_falls_back_to_opus(self):
        fable, _ = self.make(FableClient, error=api_error(529))
        opus, _ = self.make(OpusClient)
        resp = await FallbackClient(fable, opus).call("a", [{"role": "user", "content": "x"}])
        self.assertEqual(resp.model, OPUS_5)
        self.assertEqual(len(self.breaker._records), 2)   # 실패 1건(0비용) + 성공 1건

    async def test_breaker_exception_does_not_fall_back(self):
        self.breaker.record_call("x", 0, 0, 10.0)
        fable, fmock = self.make(FableClient)
        opus, omock = self.make(OpusClient)
        with self.assertRaises(BudgetExceededError):
            await FallbackClient(fable, opus).call("a", [{"role": "user", "content": "x"}])
        fmock.assert_not_called()
        omock.assert_not_called()


class FactoryTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    def test_names(self):
        self.assertIsInstance(get_client("fable"), FableClient)
        self.assertIsInstance(get_client("opus"), OpusClient)
        self.assertIsInstance(get_client("fallback"), FallbackClient)

    def test_env_var_selects(self):
        os.environ["MODEL_CLIENT"] = "opus"
        try:
            self.assertIsInstance(get_client(), OpusClient)
        finally:
            del os.environ["MODEL_CLIENT"]

    def test_default_is_fallback(self):
        os.environ.pop("MODEL_CLIENT", None)
        self.assertIsInstance(get_client(), FallbackClient)

    def test_unknown_name(self):
        with self.assertRaises(ValueError):
            get_client("gpt")


if __name__ == "__main__":
    unittest.main()
