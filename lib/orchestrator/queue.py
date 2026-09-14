"""
queue.py

asyncio.Queue 기반의 단순 작업 큐. 워커 에이전트에게 나눠 줄 작업(Task) 하나는
{id, agent_name, payload, status, result} 구조입니다.

쇼츠 파이프라인(pipeline.py)과는 독립적인 범용 큐입니다. 용도:
  - 워커 에이전트를 여러 개 두고 작업을 분배할 때
  - 실제 모델 호출 경로(큐 → 에이전트 → 모델 → breaker → 결과)를 검증할 때
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    agent_name: str
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    cost_usd: float = 0.0
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""


class TaskQueue:
    """작업을 넣고(put), 워커가 꺼내고(get), 결과를 기록(mark_done / mark_failed)합니다.

    모든 Task 는 self.tasks 에 남아 있어서 큐를 다 비운 뒤에도 결과를 모을 수 있습니다.
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue[Task] = asyncio.Queue()
        self.tasks: Dict[str, Task] = {}

    # ---- 생산자 ----

    def put(self, agent_name: str, payload: Dict[str, Any]) -> Task:
        task = Task(agent_name=agent_name, payload=payload)
        self.tasks[task.id] = task
        self._q.put_nowait(task)
        return task

    # ---- 소비자 (워커) ----

    async def get(self) -> Task:
        task = await self._q.get()
        task.status = TaskStatus.RUNNING
        task.started_at = _now()
        return task

    def mark_done(self, task: Task, result: Any, cost_usd: float = 0.0) -> None:
        task.status = TaskStatus.DONE
        task.result = result
        task.cost_usd = cost_usd
        task.finished_at = _now()
        self._q.task_done()

    def mark_failed(self, task: Task, error: str, cost_usd: float = 0.0) -> None:
        task.status = TaskStatus.FAILED
        task.error = error
        task.cost_usd = cost_usd
        task.finished_at = _now()
        self._q.task_done()

    # ---- 조회 ----

    async def join(self) -> None:
        """넣은 작업이 전부 done/failed 처리될 때까지 대기."""
        await self._q.join()

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def all(self) -> List[Task]:
        return list(self.tasks.values())

    def by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self.tasks.values() if t.status == status]

    @property
    def total_cost(self) -> float:
        return sum(t.cost_usd for t in self.tasks.values())

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)
