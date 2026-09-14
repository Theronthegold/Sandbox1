"""대시보드 API 검증. FastAPI TestClient + FakeClient + fake producer/uploader."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_orchestrator import FakeClient, all_ok, fake_produce, fake_upload, CRITIC_NO, QA_NO  # noqa: E402

from lib.dashboard import create_app


def make_client(responses=None):
    tmp = Path(tempfile.mkdtemp())
    app = create_app(
        db_path=tmp / "db.sqlite", work_root=tmp / "jobs",
        client_factory=lambda job, demo: FakeClient(responses or all_ok()),
        producer=fake_produce, uploader=fake_upload,
    )
    return TestClient(app)


def wait_status(c: TestClient, job_id: str, target: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] == target or j["status"] == "FAILED":
            return j
        time.sleep(0.1)
    raise AssertionError(f"{target} 도달 실패: {j['status']} {j.get('error')}")


class DashboardTest(unittest.TestCase):
    def test_index_and_summary(self):
        with make_client() as c:
            self.assertIn("Shorts Orchestrator", c.get("/").text)
            s = c.get("/api/summary").json()
            self.assertEqual(s["job_count"], 0)
            self.assertEqual(len(s["topics"]), 2)

    def test_create_run_approve_publish(self):
        with make_client() as c:
            j = c.post("/api/jobs", json={"topic": "money_psychology"}).json()
            self.assertEqual(j["topic_area"], "money_psychology")
            j = wait_status(c, j["id"], "AWAITING_APPROVAL")
            self.assertEqual(j["status"], "AWAITING_APPROVAL", j.get("error"))
            self.assertEqual(len(j["steps"]), 7)
            self.assertTrue(any(e["agent"] == "producer" for e in j["events"]))
            self.assertIn("publish_meta", j["artifacts"])
            self.assertNotIn("_demo", j["artifacts"])

            self.assertEqual(c.post(f"/api/jobs/{j['id']}/retry").status_code, 200)  # 승인 대기에서 retry → 다시 승인 대기
            j = wait_status(c, j["id"], "AWAITING_APPROVAL")

            r = c.post(f"/api/jobs/{j['id']}/approve")
            self.assertEqual(r.status_code, 200, r.text)
            j = wait_status(c, j["id"], "PUBLISHED")
            self.assertEqual(j["artifacts"]["video_id"], "vid123")

            s = c.get("/api/summary").json()
            self.assertEqual(s["by_status"]["PUBLISHED"], 1)
            self.assertGreater(s["total_cost_usd"], 0)

    def test_topic_alternates_when_unspecified(self):
        with make_client() as c:
            a = c.post("/api/jobs", json={}).json()
            wait_status(c, a["id"], "AWAITING_APPROVAL")
            b = c.post("/api/jobs", json={}).json()
            self.assertNotEqual(a["topic_area"], b["topic_area"])
            wait_status(c, b["id"], "AWAITING_APPROVAL")

    def test_reject_and_failed_retry(self):
        with make_client() as c:
            j = c.post("/api/jobs", json={"topic": "ai_tech"}).json()
            wait_status(c, j["id"], "AWAITING_APPROVAL")
            r = c.post(f"/api/jobs/{j['id']}/reject", json={"reason": "후킹 약함"}).json()
            self.assertEqual(r["status"], "FAILED")
            self.assertIn("후킹 약함", r["error"])
            # 거절된 Job 도 retry 하면 QA 통과 산출물이 있으므로 다시 승인 대기로
            c.post(f"/api/jobs/{j['id']}/retry")
            j = wait_status(c, j["id"], "AWAITING_APPROVAL")
            self.assertEqual(j["status"], "AWAITING_APPROVAL")

    def test_qa_failure_marks_step_failed(self):
        with make_client(all_ok(qa=QA_NO)) as c:
            j = c.post("/api/jobs", json={"topic": "ai_tech"}).json()
            j = wait_status(c, j["id"], "FAILED")
            self.assertEqual(j["status"], "FAILED")
            qa_step = [s for s in j["steps"] if s["name"] == "qa"][0]
            self.assertEqual(qa_step["status"], "failed")

    def test_approve_wrong_state_400(self):
        with make_client() as c:
            j = c.post("/api/jobs", json={"topic": "ai_tech"}).json()
            wait_status(c, j["id"], "AWAITING_APPROVAL")
            c.post(f"/api/jobs/{j['id']}/approve")
            wait_status(c, j["id"], "PUBLISHED")
            self.assertEqual(c.post(f"/api/jobs/{j['id']}/approve").status_code, 400)
            self.assertEqual(c.post(f"/api/jobs/{j['id']}/retry").status_code, 400)
            self.assertEqual(c.get("/api/jobs/nope").status_code, 404)

    def test_sse_stream_emits_events_and_status(self):
        with make_client() as c:
            j = c.post("/api/jobs", json={"topic": "money_psychology"}).json()
            wait_status(c, j["id"], "AWAITING_APPROVAL")
            r = c.get(f"/api/jobs/{j['id']}/events?after=0&ticks=1")   # ticks=1: status 한 번 보내고 종료
            self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
            lines = r.text.splitlines()
            self.assertTrue(any(l.startswith("event: event") for l in lines))
            status_idx = lines.index("event: status")
            payload = json.loads(lines[status_idx + 1][6:])
            self.assertEqual(payload["status"], "AWAITING_APPROVAL")
            self.assertFalse(payload["running"])

    def test_video_404_without_file(self):
        with make_client() as c:
            j = c.post("/api/jobs", json={"topic": "ai_tech"}).json()
            wait_status(c, j["id"], "AWAITING_APPROVAL")
            self.assertEqual(c.get(f"/api/jobs/{j['id']}/video").status_code, 404)   # fake producer 는 파일을 안 만듦
            self.assertEqual(c.get(f"/api/jobs/{j['id']}/frames/0").status_code, 404)


if __name__ == "__main__":
    unittest.main()
