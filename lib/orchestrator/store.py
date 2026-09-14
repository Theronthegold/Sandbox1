"""
store.py

aiosqlite 기반 영속화. 세 테이블:
  jobs    Job 스냅샷 (산출물 JSON 포함) - 재개와 대시보드 목록용
  steps   단계 실행 기록 (시작/종료/비용) - 진척도 시각화용
  events  타임라인 로그 (에이전트별 메시지) - 대시보드 실시간 스트림용
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from .schemas import Job, JobStatus, TopicArea

DEFAULT_DB = Path("output/orchestrator.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    topic_area TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    review_rounds INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    warnings TEXT NOT NULL DEFAULT '[]',
    artifacts TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    agent TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_job ON steps(job_id);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = Path(path)
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> "Store":
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Store":
        return await self.open()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store.open() 을 먼저 호출하세요"
        return self._db

    # ---- jobs ----

    async def save_job(self, job: Job) -> None:
        job.touch()
        await self.db.execute(
            """INSERT INTO jobs (id, topic_area, status, created_at, updated_at, cost_usd, review_rounds, error, warnings, artifacts)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                 cost_usd=excluded.cost_usd, review_rounds=excluded.review_rounds, error=excluded.error,
                 warnings=excluded.warnings, artifacts=excluded.artifacts""",
            (job.id, job.topic_area.value, job.status.value, job.created_at, job.updated_at, job.cost_usd,
             job.review_rounds, job.error, json.dumps(job.warnings, ensure_ascii=False),
             json.dumps(job.artifacts, ensure_ascii=False, default=str)),
        )
        await self.db.commit()

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_job(row) if row else None

    async def list_jobs(self, limit: int = 50) -> List[Job]:
        async with self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_job(r) for r in rows]

    async def recent_titles(self, topic_area: TopicArea, limit: int = 30) -> List[str]:
        """같은 주제로 이미 만든 쇼츠 제목 (중복 회피용). 실패한 Job 은 제외."""
        async with self.db.execute(
            "SELECT artifacts FROM jobs WHERE topic_area=? AND status!=? ORDER BY created_at DESC LIMIT ?",
            (topic_area.value, JobStatus.FAILED.value, limit),
        ) as cur:
            rows = await cur.fetchall()
        titles = []
        for r in rows:
            art = json.loads(r["artifacts"])
            t = (art.get("publish_meta") or {}).get("title") or (art.get("script") or {}).get("title_working")
            if t:
                titles.append(t)
        return titles

    # ---- steps ----

    async def start_step(self, job_id: str, name: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO steps (job_id, name, status, started_at) VALUES (?,?,?,?)",
            (job_id, name, "running", _now()),
        )
        await self.db.commit()
        return cur.lastrowid

    async def finish_step(self, step_id: int, *, status: str = "done", cost_usd: float = 0.0, detail: str = "") -> None:
        await self.db.execute(
            "UPDATE steps SET status=?, finished_at=?, cost_usd=?, detail=? WHERE id=?",
            (status, _now(), cost_usd, detail[:2000], step_id),
        )
        await self.db.commit()

    async def fail_running_steps(self, job_id: str, detail: str = "") -> None:
        """Job 이 예외로 끝났을 때 아직 running 인 단계를 failed 로 정리 (대시보드 표시용)."""
        await self.db.execute(
            "UPDATE steps SET status='failed', finished_at=?, detail=? WHERE job_id=? AND status='running'",
            (_now(), detail[:2000], job_id),
        )
        await self.db.commit()

    async def list_steps(self, job_id: str) -> List[Dict[str, Any]]:
        async with self.db.execute("SELECT * FROM steps WHERE job_id=? ORDER BY id", (job_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ---- events ----

    async def add_event(self, job_id: str, agent: str, message: str, *, level: str = "info") -> None:
        await self.db.execute(
            "INSERT INTO events (job_id, ts, level, agent, message) VALUES (?,?,?,?,?)",
            (job_id, _now(), level, agent, message[:2000]),
        )
        await self.db.commit()

    async def list_events(self, job_id: str, *, after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?", (job_id, after_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        topic_area=TopicArea(row["topic_area"]),
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cost_usd=row["cost_usd"],
        review_rounds=row["review_rounds"],
        error=row["error"],
        warnings=json.loads(row["warnings"]),
        artifacts=json.loads(row["artifacts"]),
    )
