"""
app.py

대시보드 서버. 파이프라인을 이 프로세스 안에서 백그라운드 태스크로 돌리므로
진행 상황이 실시간으로 보이고, 승인 버튼이 바로 업로드 단계를 이어갑니다.

API
  GET  /                          대시보드 페이지
  GET  /api/summary               전체 비용, 상태별 개수, 실행 중 Job
  GET  /api/jobs                  Job 목록
  POST /api/jobs                  {topic?, demo?} → Job 생성 + 백그라운드 실행
  GET  /api/jobs/{id}             Job + steps + events
  GET  /api/jobs/{id}/events      SSE (event: event | status)
  POST /api/jobs/{id}/approve     승인 → 업로드 진행
  POST /api/jobs/{id}/reject      {reason}
  POST /api/jobs/{id}/retry       실패 지점부터 재개
  GET  /api/jobs/{id}/video       렌더된 mp4
  GET  /api/jobs/{id}/frames/{i}  QA 프레임
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from lib.model_client import ModelClient, get_client
from lib.orchestrator import Job, JobStatus, Pipeline, Store, TopicArea, resume_status
from lib.orchestrator.demo import DemoClient
from lib.orchestrator.producer import produce
from lib.orchestrator.schemas import TERMINAL, TOPIC_LABELS
from lib.tools.stock import PexelsClient

STATIC_DIR = Path(__file__).parent / "static"

ClientFactory = Callable[[Job, bool], ModelClient]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class NewJobRequest(BaseModel):
    topic: Optional[str] = None      # money_psychology | ai_tech | None(번갈아)
    demo: bool = False


class RejectRequest(BaseModel):
    reason: str = ""


def create_app(
    *,
    db_path: Path = Path("output/orchestrator.db"),
    work_root: Path = Path("output/jobs"),
    per_job_budget_usd: float = 1.5,
    client_factory: Optional[ClientFactory] = None,
    producer: Callable[..., Awaitable[Any]] = produce,
    uploader: Optional[Callable[..., Awaitable[str]]] = None,
) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = await Store(db_path).open()
        app.state.tasks: Dict[str, asyncio.Task] = {}
        yield
        for t in app.state.tasks.values():
            t.cancel()
        await app.state.store.close()

    app = FastAPI(title="Shorts Orchestrator", lifespan=lifespan)

    # ------------------------------------------------------------ helpers

    def store() -> Store:
        return app.state.store

    def is_running(job_id: str) -> bool:
        t = app.state.tasks.get(job_id)
        return bool(t and not t.done())

    def make_pipeline(job: Job, demo: bool) -> Pipeline:
        if client_factory:
            client = client_factory(job, demo)
        elif demo:
            client = DemoClient(job.topic_area)
        else:
            client = get_client()
        stock = PexelsClient() if os.environ.get("PEXELS_API_KEY") else None
        return Pipeline(
            store(), client, stock=stock, work_root=work_root,
            per_job_budget_usd=per_job_budget_usd, producer=producer, uploader=uploader,
        )

    def launch(job: Job) -> None:
        if is_running(job.id):
            raise HTTPException(409, "이미 실행 중입니다")
        demo = bool(job.artifacts.get("_demo"))
        app.state.tasks[job.id] = asyncio.create_task(make_pipeline(job, demo).run(job))

    async def get_or_404(job_id: str) -> Job:
        job = await store().get_job(job_id)
        if not job:
            raise HTTPException(404, "job 없음")
        return job

    def job_summary(job: Job) -> Dict[str, Any]:
        meta = job.artifacts.get("publish_meta") or {}
        script = job.artifacts.get("script") or {}
        return {
            "id": job.id,
            "topic_area": job.topic_area.value,
            "topic_label": TOPIC_LABELS[job.topic_area],
            "status": job.status.value,
            "cost_usd": round(job.cost_usd, 4),
            "review_rounds": job.review_rounds,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "title": meta.get("title") or script.get("title_working") or "",
            "error": job.error,
            "warnings": job.warnings,
            "demo": bool(job.artifacts.get("_demo")),
            "running": is_running(job.id),
        }

    # ------------------------------------------------------------ routes

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/summary")
    async def summary() -> Dict[str, Any]:
        jobs = await store().list_jobs(limit=500)
        by_status: Dict[str, int] = {}
        for j in jobs:
            by_status[j.status.value] = by_status.get(j.status.value, 0) + 1
        tasks_cost = await store().tasks_total_cost()
        return {
            "total_cost_usd": round(sum(j.cost_usd for j in jobs) + tasks_cost, 4),
            "tasks_cost_usd": round(tasks_cost, 4),
            "task_count": len(await store().list_tasks(limit=10000)),
            "job_count": len(jobs),
            "by_status": by_status,
            "per_job_budget_usd": per_job_budget_usd,
            "running": [jid for jid in app.state.tasks if is_running(jid)],
            "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "has_pexels_key": bool(os.environ.get("PEXELS_API_KEY")),
            "topics": [{"value": t.value, "label": TOPIC_LABELS[t]} for t in TopicArea],
        }

    # ---- 워커 큐 작업 (queue.py / worker.py) ----

    @app.get("/api/tasks")
    async def list_tasks() -> Dict[str, Any]:
        tasks = await store().list_tasks()
        return {"tasks": tasks, "total_cost_usd": round(await store().tasks_total_cost(), 4), "count": len(tasks)}

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_page() -> str:
        data = await list_tasks()
        rows = "".join(
            f"<tr><td>{t['id']}</td><td>{t['agent_name']}</td><td>{t['status']}</td>"
            f"<td>{_esc(json.dumps(t['payload'], ensure_ascii=False))[:120]}</td>"
            f"<td>{_esc(json.dumps(t['result'], ensure_ascii=False) if t['result'] is not None else t['error'])[:200]}</td>"
            f"<td style='text-align:right'>${t['cost_usd']:.4f}</td><td>{t['finished_at'] or t['started_at'] or t['created_at']}</td></tr>"
            for t in data["tasks"]
        )
        return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<title>워커 작업</title><style>body{{font:14px -apple-system,'Segoe UI','Malgun Gothic',sans-serif;margin:24px;color:#182029}}
table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #d9dee6;padding:6px 8px;text-align:left;vertical-align:top;font-size:13px}}
th{{color:#5b6675;font-weight:500}}a{{color:#2457c5}}</style></head><body>
<p><a href="/">← 쇼츠 대시보드</a></p>
<h2 style="margin:0 0 4px">워커 작업 {data['count']}건 · 누적 비용 ${data['total_cost_usd']:.4f}</h2>
<p style="color:#5b6675;margin:0 0 14px">3초마다 새로고침 · <code>python -m scripts.smoke_test</code> 로 작업을 넣습니다</p>
<table><tr><th>id</th><th>agent</th><th>status</th><th>payload</th><th>result / error</th><th>cost</th><th>time</th></tr>{rows or '<tr><td colspan=7>작업 없음</td></tr>'}</table>
</body></html>"""

    @app.get("/api/jobs")
    async def list_jobs() -> list:
        return [job_summary(j) for j in await store().list_jobs()]

    @app.post("/api/jobs")
    async def new_job(req: NewJobRequest) -> Dict[str, Any]:
        if req.topic:
            topic = TopicArea(req.topic)
        else:
            last = await store().list_jobs(limit=1)
            topic = TopicArea.ai_tech if last and last[0].topic_area == TopicArea.money_psychology else TopicArea.money_psychology
        job = Job(topic_area=topic)
        if req.demo:
            job.artifacts["_demo"] = True
        await store().save_job(job)
        launch(job)
        return job_summary(job)

    @app.get("/api/jobs/{job_id}")
    async def job_detail(job_id: str) -> Dict[str, Any]:
        job = await get_or_404(job_id)
        return {
            **job_summary(job),
            "artifacts": {k: v for k, v in job.artifacts.items() if not k.startswith("_")},
            "steps": await store().list_steps(job_id),
            "events": await store().list_events(job_id, limit=300),
        }

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, after: int = 0, ticks: int = 0) -> StreamingResponse:
        """ticks > 0 이면 그 횟수만큼 status 를 보내고 스트림을 닫음 (테스트/폴링용). 0 이면 무한."""
        await get_or_404(job_id)

        async def gen():
            last = after
            idle_ticks = 0
            sent = 0
            while ticks <= 0 or sent < ticks:
                for e in await store().list_events(job_id, after_id=last):
                    last = e["id"]
                    idle_ticks = 0
                    yield f"id: {e['id']}\nevent: event\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                job = await store().get_job(job_id)
                if job is None:
                    return
                payload = {"status": job.status.value, "cost_usd": round(job.cost_usd, 4), "running": is_running(job_id)}
                yield f"event: status\ndata: {json.dumps(payload)}\n\n"
                sent += 1
                idle_ticks += 1
                if ticks > 0 and sent >= ticks:
                    return
                # 끝난 Job 은 느리게, 실행 중이면 빠르게 폴링
                await asyncio.sleep(0.6 if is_running(job_id) else min(5.0, 1.0 + idle_ticks * 0.5))

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/jobs/{job_id}/approve")
    async def approve(job_id: str) -> Dict[str, Any]:
        job = await get_or_404(job_id)
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise HTTPException(400, f"승인 가능한 상태가 아닙니다: {job.status.value}")
        pipeline = make_pipeline(job, bool(job.artifacts.get("_demo")))
        await pipeline.approve(job)
        launch(job)
        return job_summary(job)

    @app.post("/api/jobs/{job_id}/reject")
    async def reject(job_id: str, req: RejectRequest) -> Dict[str, Any]:
        job = await get_or_404(job_id)
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise HTTPException(400, f"거절 가능한 상태가 아닙니다: {job.status.value}")
        await make_pipeline(job, bool(job.artifacts.get("_demo"))).reject(job, req.reason or "사유 없음")
        return job_summary(job)

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: str) -> Dict[str, Any]:
        job = await get_or_404(job_id)
        if job.status not in TERMINAL and is_running(job_id):
            raise HTTPException(409, "실행 중입니다")
        if job.status == JobStatus.PUBLISHED:
            raise HTTPException(400, "이미 완료된 Job 입니다")
        job.status = resume_status(job)
        job.error = ""
        await store().save_job(job)
        launch(job)
        return job_summary(job)

    @app.get("/api/jobs/{job_id}/video")
    async def video(job_id: str) -> FileResponse:
        job = await get_or_404(job_id)
        path = Path((job.artifacts.get("produce") or {}).get("video_path", ""))
        if not path.is_file():
            raise HTTPException(404, "영상 없음")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/jobs/{job_id}/frames/{index}")
    async def frame(job_id: str, index: int) -> FileResponse:
        job = await get_or_404(job_id)
        frames = (job.artifacts.get("produce") or {}).get("frame_paths", [])
        if index < 0 or index >= len(frames) or not Path(frames[index]).is_file():
            raise HTTPException(404, "프레임 없음")
        return FileResponse(frames[index], media_type="image/jpeg")

    return app
