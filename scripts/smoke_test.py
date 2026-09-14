"""
smoke_test.py - "큐 → 에이전트 → 모델 호출 → circuit_breaker → 결과 → 대시보드" 경로 검증

    python -m scripts.smoke_test            # 실제 모델 (ANTHROPIC_API_KEY 필요, 3건 합쳐 1센트 안팎)
    python -m scripts.smoke_test --demo     # 키 없이 가짜 응답으로 경로만 확인

가짜 작업 3개를 큐에 넣고 EchoAgent 워커로 처리한 뒤, 결과와 비용을 출력하고
output/orchestrator.db 에 기록합니다. 대시보드(python -m scripts.serve)의
http://127.0.0.1:8000/tasks 에서 같은 내용을 볼 수 있습니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from lib.circuit_breaker import breaker
from lib.model_client import ModelClient, ModelResponse, get_client
from lib.orchestrator.agents.echo_agent import EchoAgent
from lib.orchestrator.queue import TaskQueue
from lib.orchestrator.store import Store
from lib.orchestrator.worker import run_until_empty

SAMPLES = [
    "월급이 들어오면 쓰는 게 먼저고 남는 게 저축이 되는 이유는 뇌가 지금의 만족을 미래보다 크게 느끼는 현재 편향 때문이다.",
    "코딩 에이전트는 코드를 고치고 테스트를 돌리고 실패 메시지를 읽은 뒤 다시 고치는 반복을 사람 대신 수십 번 수행한다.",
    "세일 표시는 정가를 기준점으로 만들어 실제보다 싸게 느끼게 하는 앵커링 효과를 이용한다.",
]


class EchoDemoClient(ModelClient):
    """키 없이 경로만 확인할 때 쓰는 가짜 모델. 첫 문장 앞부분을 요약으로 돌려줌."""

    @property
    def model(self) -> str:
        return "demo"

    async def call(self, agent_name, messages, *, system=None, max_tokens=None, **extra) -> ModelResponse:
        await asyncio.sleep(0.3)
        text_in = json.loads(messages[0]["content"])["text"]
        out = json.dumps({"summary": text_in[:30] + "…", "char_count": len(text_in)}, ensure_ascii=False)
        # 가짜 호출도 breaker 를 거치게 해서 대시보드 비용/루프 감지 경로까지 검증
        breaker.check_before_call(agent_name)
        breaker.record_call(agent_name, 120, 30, 0.0027)
        return ModelResponse(text=out, model="demo", stop_reason="end_turn", input_tokens=120, output_tokens=30,
                             cost_usd=0.0027, raw=SimpleNamespace(content=[SimpleNamespace(type="text", text=out)]))


async def main(demo: bool, db: Path) -> None:
    client = EchoDemoClient() if demo else get_client()
    queue = TaskQueue()
    for text in SAMPLES:
        queue.put("echo", {"text": text})
    print(f"큐에 {queue.pending}개 작업 투입 (client={client.model})")

    async with Store(db) as store:
        tasks = await run_until_empty(queue, {"echo": EchoAgent(client)}, store=store, concurrency=2)

    print()
    for t in tasks:
        mark = "OK " if t.status.value == "DONE" else "ERR"
        body = t.result["summary"] if t.result else t.error
        print(f"[{mark}] {t.id}  ${t.cost_usd:.4f}  {body}")
    print(f"\n작업 비용 합계: ${queue.total_cost:.4f}   breaker 누적: ${breaker.total_cost:.4f}   DB: {db}")
    print("대시보드에서 보기: python -m scripts.serve → http://127.0.0.1:8000/tasks")


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level="WARNING")
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--db", default=Path("output/orchestrator.db"), type=Path)
    a = p.parse_args()
    asyncio.run(main(a.demo, a.db))
