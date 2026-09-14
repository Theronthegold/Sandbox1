"""queue → worker → EchoAgent → (FakeClient) → store 경로 검증."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_orchestrator import FakeClient, PROMPTS  # noqa: E402

from fastapi.testclient import TestClient

from lib.circuit_breaker import BudgetExceededError, CircuitBreaker
from lib.dashboard import create_app
from lib.model_client import FableClient
from lib.orchestrator.agents.echo_agent import EchoAgent
from lib.orchestrator.queue import TaskQueue, TaskStatus
from lib.orchestrator.store import Store
from lib.orchestrator.worker import run_until_empty

ECHO = {"summary": "요약입니다", "char_count": 12}


class QueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_put_get_done_failed(self):
        q = TaskQueue()
        a = q.put("echo", {"text": "a"})
        b = q.put("echo", {"text": "b"})
        self.assertEqual(q.pending, 2)
        t = await q.get()
        self.assertEqual(t.status, TaskStatus.RUNNING)
        q.mark_done(t, {"x": 1}, 0.5)
        t2 = await q.get()
        q.mark_failed(t2, "boom")
        await q.join()
        self.assertEqual(a.status, TaskStatus.DONE)
        self.assertEqual(b.status, TaskStatus.FAILED)
        self.assertAlmostEqual(q.total_cost, 0.5)
        self.assertEqual(len(q.by_status(TaskStatus.DONE)), 1)


class WorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_tasks_flow_to_store(self):
        q = TaskQueue()
        for i in range(3):
            q.put("echo", {"text": f"text {i}"})
        q.put("nope", {"text": "x"})                       # 등록 안 된 에이전트 → FAILED
        client = FakeClient({"echo": ECHO}, cost=0.004)
        with tempfile.TemporaryDirectory() as d:
            async with Store(Path(d) / "t.db") as store:
                tasks = await run_until_empty(q, {"echo": EchoAgent(client, PROMPTS)}, store=store, concurrency=2)
                rows = await store.list_tasks()
                total = await store.tasks_total_cost()
        done = [t for t in tasks if t.status == TaskStatus.DONE]
        self.assertEqual(len(done), 3)
        self.assertEqual(done[0].result["summary"], "요약입니다")
        failed = [t for t in tasks if t.status == TaskStatus.FAILED][0]
        self.assertIn("등록되지 않은", failed.error)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(total, 0.012)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0]["extra"]["output_config"]["effort"], "low")

    async def test_breaker_blocks_real_client_path(self):
        """실제 클라이언트 클래스 + 예산 소진 breaker: API 호출 전에 차단되어 FAILED 로 기록."""
        b = CircuitBreaker(max_total_cost_usd=0.01)
        b.record_call("x", 0, 0, 0.01)
        import os
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
        client = FableClient(breaker=b)
        q = TaskQueue()
        q.put("echo", {"text": "hi"})
        tasks = await run_until_empty(q, {"echo": EchoAgent(client, PROMPTS)}, concurrency=1)
        self.assertEqual(tasks[0].status, TaskStatus.FAILED)
        self.assertIn(BudgetExceededError.__name__, tasks[0].error)


class DashboardTasksTest(unittest.TestCase):
    def test_tasks_api_and_page(self):
        tmp = Path(tempfile.mkdtemp())
        app = create_app(db_path=tmp / "db.sqlite", work_root=tmp / "jobs")
        with TestClient(app) as c:
            self.assertEqual(c.get("/api/tasks").json()["count"], 0)
            # 워커로 작업 넣기 (같은 DB)
            async def fill():
                q = TaskQueue()
                q.put("echo", {"text": "hello"})
                async with Store(tmp / "db.sqlite") as store:
                    await run_until_empty(q, {"echo": EchoAgent(FakeClient({"echo": ECHO}, cost=0.003), PROMPTS)}, store=store)
            import asyncio
            asyncio.run(fill())
            data = c.get("/api/tasks").json()
            self.assertEqual(data["count"], 1)
            self.assertAlmostEqual(data["total_cost_usd"], 0.003)
            self.assertEqual(data["tasks"][0]["result"]["summary"], "요약입니다")
            page = c.get("/tasks").text
            self.assertIn("요약입니다", page)
            s = c.get("/api/summary").json()
            self.assertEqual(s["task_count"], 1)
            self.assertAlmostEqual(s["total_cost_usd"], 0.003)


if __name__ == "__main__":
    unittest.main()
