"""
worker.py

큐에서 작업을 꺼내 이름이 맞는 에이전트에게 주고 결과를 모으는 루프.

모델 호출은 전부 Agent.run() → ModelClient.call() 을 거치므로
circuit_breaker.check_before_call() / record_call() 이 자동으로 적용됩니다.
breaker 가 차단하면(BudgetExceededError 등) 그 작업은 FAILED 로 기록되고 루프는 계속됩니다.

    queue = TaskQueue()
    queue.put("echo", {"text": "..."})
    await run_until_empty(queue, {"echo": EchoAgent(client)}, store=store)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from .agents.base_agent import Agent
from .queue import Task, TaskQueue
from .store import Store

logger = logging.getLogger("orchestrator.worker")


async def process_task(task: Task, agents: Dict[str, Agent], queue: TaskQueue) -> None:
    agent = agents.get(task.agent_name)
    try:
        if agent is None:
            raise KeyError(f"등록되지 않은 에이전트 '{task.agent_name}'")
        input_model = getattr(agent, "input_model", None)
        payload = input_model.model_validate(task.payload) if input_model else task.payload
        out, cost = await agent.run(payload)
        queue.mark_done(task, out.model_dump(), cost)
        logger.info("[%s] %s done ($%.4f)", task.id, task.agent_name, cost)
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 작업 단위로 기록
        queue.mark_failed(task, f"{type(e).__name__}: {e}")
        logger.warning("[%s] %s failed: %s", task.id, task.agent_name, e)


async def run_worker(queue: TaskQueue, agents: Dict[str, Agent], store: Optional[Store] = None, *, name: str = "worker") -> None:
    """무한 루프. 취소될 때까지 큐에서 작업을 꺼내 처리."""
    while True:
        task = await queue.get()
        if store:
            await store.save_task(task)          # RUNNING 기록
        await process_task(task, agents, queue)
        if store:
            await store.save_task(task)          # DONE / FAILED + 비용 기록


async def run_until_empty(
    queue: TaskQueue,
    agents: Dict[str, Agent],
    store: Optional[Store] = None,
    *,
    concurrency: int = 2,
) -> List[Task]:
    """큐가 빌 때까지 워커 concurrency 개로 처리하고 모든 Task 를 돌려줍니다."""
    if store:
        for t in queue.all():
            await store.save_task(t)             # PENDING 부터 대시보드에 보이게
    workers = [asyncio.create_task(run_worker(queue, agents, store, name=f"worker-{i}")) for i in range(concurrency)]
    try:
        await queue.join()
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    if store:
        # join() 은 task_done() 직후 풀리므로 워커의 마지막 save 가 취소될 수 있음 → 최종 상태를 확정 저장
        for t in queue.all():
            await store.save_task(t)
    return queue.all()
