"""오케스트레이터 검증. 모델 호출은 FakeClient, 렌더/업로드는 fake 로 대체."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from lib.model_client import ModelClient, ModelResponse
from lib.orchestrator import Job, JobBudgetExceeded, JobStatus, Pipeline, Store, TopicArea
from lib.orchestrator.agents import AgentOutputError, Writer
from lib.orchestrator.producer import ProduceResult
from lib.orchestrator.schemas import ResearchOutput, Script, estimate_narration_seconds

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

# ---- canned outputs ----

CANDIDATE = {
    "title_idea": "월급이 스치는 이유", "hook": "월급이 통장을 스치는 이유?", "angle": "현재 편향",
    "why_now": "월급날 직후", "sources": ["https://example.org/study"], "risk": "low",
}
RESEARCH = {"candidates": [CANDIDATE, CANDIDATE, CANDIDATE], "picked_index": 1, "rationale": "후킹 강함"}
SCRIPT = {
    "title_working": "월급이 스치는 이유",
    "scenes": [{"narration": "월급이 통장을 스치는 이유, 아세요?"},
               {"narration": "뇌는 지금의 만족을 훨씬 크게 느낍니다."},
               {"narration": "그래서 월급날 가장 먼저 자기에게 송금하세요."},
               {"narration": "여러분은 어떤 쪽인가요? 댓글로 알려주세요."}],
}
CRITIC_OK = {"approve": True, "score": 85, "issues": [], "summary": "좋음"}
CRITIC_NO = {"approve": False, "score": 50, "issues": [{"scene_index": 1, "kind": "fact", "detail": "근거 없음", "fix": "출처 추가"}], "summary": "근거를 넣으세요"}
DIRECTED = {
    "scenes": [{"narration": s["narration"], "stock_query": f"query {i}", "emphasis": [], "preset": "pop"} for i, s in enumerate(SCRIPT["scenes"])],
    "mood": "calm",
}
PUBLISH = {"title": "월급이 스치는 이유 #Shorts", "description": "설명", "hashtags": ["재테크", "돈의심리학"]}
QA_OK = {"passed": True, "issues": []}
QA_NO = {"passed": False, "issues": [{"severity": "block", "scene_index": 0, "detail": "워터마크"}]}


class FakeClient(ModelClient):
    """agent_name 별로 준비된 응답(문자열 또는 리스트)을 순서대로 돌려준다."""

    def __init__(self, responses: Dict[str, Any], cost: float = 0.05):
        self.responses = {k: (list(v) if isinstance(v, list) else [v]) for k, v in responses.items()}
        self.cost = cost
        self.calls: List[Dict[str, Any]] = []

    @property
    def model(self) -> str:
        return "fake"

    async def call(self, agent_name, messages, *, system=None, max_tokens=None, **extra) -> ModelResponse:
        self.calls.append({"agent": agent_name, "messages": messages, "extra": extra, "system": system})
        queue = self.responses[agent_name]
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return ModelResponse(
            text=text, model="fake", stop_reason="end_turn", input_tokens=10, output_tokens=10,
            cost_usd=self.cost, raw=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)]),
        )


def all_ok(**overrides) -> Dict[str, Any]:
    base = {"researcher": RESEARCH, "writer": SCRIPT, "critic": CRITIC_OK, "director": DIRECTED, "publisher": PUBLISH, "qa": QA_OK}
    base.update(overrides)
    return base


async def fake_produce(directed, work_dir, *, stock=None, on_event=None, **_) -> ProduceResult:
    if on_event:
        await on_event("fake render")
    return ProduceResult(video_path=Path(work_dir) / "short.mp4", duration=18.0,
                         frame_paths=[], scene_durations=[4.5] * len(directed.scenes), clip_sources=["placeholder"])


async def fake_upload(video, *, title, description, tags) -> str:
    return "vid123"


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = await Store(Path(self.tmp) / "t.db").open()

    async def asyncTearDown(self):
        await self.store.close()

    def pipeline(self, client, **kw) -> Pipeline:
        kw.setdefault("producer", fake_produce)
        kw.setdefault("uploader", fake_upload)
        kw.setdefault("work_root", Path(self.tmp) / "jobs")
        return Pipeline(self.store, client, prompts_dir=PROMPTS, **kw)


class AgentTest(_Base):
    async def test_sends_schema_effort_and_prompt(self):
        client = FakeClient(all_ok())
        w = Writer(client, PROMPTS)
        from lib.orchestrator.schemas import WriterInput, Candidate
        out, cost = await w.run(WriterInput(topic_label="t", candidate=Candidate(**CANDIDATE)))
        self.assertIsInstance(out, Script)
        self.assertAlmostEqual(cost, 0.05)
        call = client.calls[0]
        oc = call["extra"]["output_config"]
        self.assertEqual(oc["effort"], "high")
        self.assertEqual(oc["format"]["type"], "json_schema")
        self.assertFalse(oc["format"]["schema"].get("additionalProperties", True))
        self.assertIn("쇼츠 대본 작가", call["system"])

    async def test_retries_once_on_bad_json_then_fails(self):
        client = FakeClient({"writer": ["not json", "still not json", "x"]})
        w = Writer(client, PROMPTS)
        from lib.orchestrator.schemas import WriterInput, Candidate
        with self.assertRaises(AgentOutputError):
            await w.run(WriterInput(topic_label="t", candidate=Candidate(**CANDIDATE)))
        self.assertEqual(len(client.calls), 2)

    async def test_recovers_on_second_try(self):
        client = FakeClient({"writer": ["garbage", SCRIPT]})
        w = Writer(client, PROMPTS)
        from lib.orchestrator.schemas import WriterInput, Candidate
        out, cost = await w.run(WriterInput(topic_label="t", candidate=Candidate(**CANDIDATE)))
        self.assertEqual(len(out.scenes), 4)
        self.assertAlmostEqual(cost, 0.10)


class PipelineTest(_Base):
    async def test_full_run_to_approval_then_publish(self):
        client = FakeClient(all_ok())
        p = self.pipeline(client)
        job = await p.run(Job(topic_area=TopicArea.money_psychology))

        self.assertEqual(job.status, JobStatus.AWAITING_APPROVAL, job.error)
        # researcher, writer, critic, director, publisher, qa = 6 calls × 0.05
        self.assertAlmostEqual(job.cost_usd, 0.30)
        self.assertEqual(job.artifacts["publish_meta"]["title"], PUBLISH["title"])
        self.assertEqual(job.artifacts["produce"]["duration"], 18.0)
        names = [s["name"] for s in await self.store.list_steps(job.id)]
        self.assertEqual(names, ["research", "write#0", "review#0", "direct", "publish_meta", "produce", "qa"])
        self.assertTrue(all(s["status"] == "done" for s in await self.store.list_steps(job.id)))

        await p.approve(job)
        job = await p.run(job)
        self.assertEqual(job.status, JobStatus.PUBLISHED)
        self.assertEqual(job.artifacts["video_id"], "vid123")
        self.assertEqual((await self.store.get_job(job.id)).status, JobStatus.PUBLISHED)

    async def test_review_loop_then_proceed_with_warning(self):
        client = FakeClient(all_ok(critic=[CRITIC_NO, CRITIC_NO, CRITIC_NO]))
        p = self.pipeline(client, max_review_rounds=2)
        job = await p.run(Job(topic_area=TopicArea.ai_tech))
        self.assertEqual(job.status, JobStatus.AWAITING_APPROVAL, job.error)
        self.assertEqual(job.review_rounds, 2)
        self.assertEqual(len(job.warnings), 1)
        writer_calls = [c for c in client.calls if c["agent"] == "writer"]
        self.assertEqual(len(writer_calls), 2)
        # 두 번째 writer 호출에는 critic 피드백이 들어가야 함
        self.assertIn("근거를 넣으세요", writer_calls[1]["messages"][0]["content"])

    async def test_review_loop_approves_on_second_round(self):
        client = FakeClient(all_ok(critic=[CRITIC_NO, CRITIC_OK]))
        job = await self.pipeline(client).run(Job(topic_area=TopicArea.ai_tech))
        self.assertEqual(job.status, JobStatus.AWAITING_APPROVAL, job.error)
        self.assertEqual(job.review_rounds, 1)
        self.assertEqual(job.warnings, [])

    async def test_budget_exceeded_fails_job(self):
        client = FakeClient(all_ok(), cost=0.4)
        job = await self.pipeline(client, per_job_budget_usd=1.0).run(Job(topic_area=TopicArea.money_psychology))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("JobBudgetExceeded", job.error)
        self.assertGreater(job.cost_usd, 1.0)

    async def test_qa_block_fails_job(self):
        client = FakeClient(all_ok(qa=QA_NO))
        job = await self.pipeline(client).run(Job(topic_area=TopicArea.money_psychology))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("워터마크", job.error)

    async def test_director_scene_count_mismatch_fails(self):
        bad = {"scenes": DIRECTED["scenes"][:2], "mood": "calm"}
        job = await self.pipeline(FakeClient(all_ok(director=bad))).run(Job(topic_area=TopicArea.money_psychology))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("씬 수", job.error)

    async def test_resume_from_directing_skips_earlier_steps(self):
        client = FakeClient(all_ok())
        job = Job(topic_area=TopicArea.money_psychology, status=JobStatus.DIRECTING)
        job.artifacts["research"] = RESEARCH
        job.artifacts["script"] = SCRIPT
        job = await self.pipeline(client).run(job)
        self.assertEqual(job.status, JobStatus.AWAITING_APPROVAL, job.error)
        self.assertNotIn("researcher", [c["agent"] for c in client.calls])
        self.assertNotIn("writer", [c["agent"] for c in client.calls])

    async def test_no_approval_mode_publishes_directly(self):
        job = await self.pipeline(FakeClient(all_ok()), approval_required=False).run(Job(topic_area=TopicArea.ai_tech))
        self.assertEqual(job.status, JobStatus.PUBLISHED, job.error)

    async def test_events_recorded_for_dashboard(self):
        job = await self.pipeline(FakeClient(all_ok())).run(Job(topic_area=TopicArea.ai_tech))
        events = await self.store.list_events(job.id)
        agents = {e["agent"] for e in events}
        self.assertTrue({"researcher", "writer", "critic", "director", "publisher", "producer", "qa", "pipeline"} <= agents)


class StoreTest(_Base):
    async def test_roundtrip_and_recent_titles(self):
        j = Job(topic_area=TopicArea.ai_tech, status=JobStatus.PUBLISHED)
        j.artifacts["publish_meta"] = PUBLISH
        j.warnings.append("w")
        await self.store.save_job(j)
        back = await self.store.get_job(j.id)
        self.assertEqual(back.status, JobStatus.PUBLISHED)
        self.assertEqual(back.warnings, ["w"])
        self.assertEqual(await self.store.recent_titles(TopicArea.ai_tech), [PUBLISH["title"]])
        self.assertEqual(await self.store.recent_titles(TopicArea.money_psychology), [])

        failed = Job(topic_area=TopicArea.ai_tech, status=JobStatus.FAILED)
        failed.artifacts["script"] = SCRIPT
        await self.store.save_job(failed)
        self.assertEqual(len(await self.store.recent_titles(TopicArea.ai_tech)), 1)  # FAILED 제외


class SchemaTest(unittest.TestCase):
    def test_estimate_seconds(self):
        s = Script.model_validate(SCRIPT)
        est = estimate_narration_seconds(s)
        self.assertGreater(est, 8)
        self.assertLess(est, 20)

    def test_schema_has_no_additional_properties(self):
        schema = ResearchOutput.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["Candidate"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
