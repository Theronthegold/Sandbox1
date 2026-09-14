"""
run_job.py - 오케스트레이터 CLI

    python -m scripts.run_job new --topic money_psychology      # Job 생성 + 승인 대기까지 실행
    python -m scripts.run_job new                               # 주제 미지정 → 두 주제를 번갈아
    python -m scripts.run_job --demo new                        # API 키 없이 데모 (TTS/렌더는 실제)
    python -m scripts.run_job run <job_id>                      # 실패/중단 지점부터 재개
    python -m scripts.run_job approve <job_id>                  # 승인 → 업로드
    python -m scripts.run_job reject <job_id> "이유"
    python -m scripts.run_job list
    python -m scripts.run_job show <job_id>                     # 단계/이벤트 타임라인

환경변수(.env 지원): ANTHROPIC_API_KEY (필수), PEXELS_API_KEY (없으면 단색 placeholder 클립),
MODEL_CLIENT (fallback|fable|opus), YOUTUBE_CLIENT_SECRET (업로드 시)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from lib.circuit_breaker import breaker
from lib.model_client import get_client
from lib.orchestrator import Job, JobStatus, Pipeline, Store, TopicArea, resume_status
from lib.orchestrator.demo import DemoClient
from lib.tools.stock import PexelsClient


def _build_pipeline(store: Store, args, topic: TopicArea = TopicArea.money_psychology) -> Pipeline:
    stock = PexelsClient() if os.environ.get("PEXELS_API_KEY") else None
    if stock is None:
        print("[warn] PEXELS_API_KEY 없음 → 단색 placeholder 클립으로 렌더링합니다.", file=sys.stderr)
    if args.demo:
        print("[demo] API 키 없이 준비된 응답으로 실행합니다 (TTS/렌더는 실제).", file=sys.stderr)
        client = DemoClient(topic)
    else:
        client = get_client()
    return Pipeline(
        store, client, stock=stock,
        per_job_budget_usd=args.budget, approval_required=not args.auto_publish,
    )


async def _pick_topic(store: Store, explicit: str | None) -> TopicArea:
    if explicit:
        return TopicArea(explicit)
    jobs = await store.list_jobs(limit=1)
    if jobs and jobs[0].topic_area == TopicArea.money_psychology:
        return TopicArea.ai_tech
    return TopicArea.money_psychology


async def cmd_new(args) -> None:
    async with Store(args.db) as store:
        topic = await _pick_topic(store, args.topic)
        job = Job(topic_area=topic)
        print(f"job {job.id} ({topic.value}) 시작")
        job = await _build_pipeline(store, args, topic).run(job)
        _print_job(job)
        print(f"전역 누적 비용: ${breaker.total_cost:.3f}")


async def cmd_run(args) -> None:
    async with Store(args.db) as store:
        job = await store.get_job(args.job_id)
        if not job:
            sys.exit(f"job {args.job_id} 없음")
        if job.status == JobStatus.FAILED:
            # 실패한 단계부터 재개: 상태를 실패 직전 단계로 되돌림
            job.status = resume_status(job)
            job.error = ""
        job = await _build_pipeline(store, args, job.topic_area).run(job)
        _print_job(job)


async def cmd_approve(args) -> None:
    async with Store(args.db) as store:
        job = await store.get_job(args.job_id)
        if not job:
            sys.exit(f"job {args.job_id} 없음")
        pipeline = _build_pipeline(store, args, job.topic_area)
        await pipeline.approve(job)
        job = await pipeline.run(job)
        _print_job(job)


async def cmd_reject(args) -> None:
    async with Store(args.db) as store:
        job = await store.get_job(args.job_id)
        if not job:
            sys.exit(f"job {args.job_id} 없음")
        await _build_pipeline(store, args, job.topic_area).reject(job, args.reason)
        _print_job(job)


async def cmd_list(args) -> None:
    async with Store(args.db) as store:
        for j in await store.list_jobs():
            title = (j.artifacts.get("publish_meta") or {}).get("title") or (j.artifacts.get("script") or {}).get("title_working", "")
            print(f"{j.id}  {j.status.value:18s} {j.topic_area.value:16s} ${j.cost_usd:.3f}  {title}")


async def cmd_show(args) -> None:
    async with Store(args.db) as store:
        job = await store.get_job(args.job_id)
        if not job:
            sys.exit(f"job {args.job_id} 없음")
        _print_job(job)
        print("\n[steps]")
        for s in await store.list_steps(job.id):
            print(f"  {s['name']:16s} {s['status']:8s} ${s['cost_usd']:.3f}  {s['started_at']} → {s['finished_at'] or '...'}  {s['detail']}")
        print("\n[events]")
        for e in await store.list_events(job.id):
            print(f"  {e['ts']} [{e['level']:5s}] {e['agent']:10s} {e['message']}")


def _print_job(job: Job) -> None:
    print(f"\njob {job.id}: {job.status.value}  cost=${job.cost_usd:.3f}  rounds={job.review_rounds}")
    if job.error:
        print(f"  error: {job.error}")
    for w in job.warnings:
        print(f"  warn: {w}")
    meta = job.artifacts.get("publish_meta")
    if meta:
        print(f"  title: {meta['title']}")
    prod = job.artifacts.get("produce")
    if prod:
        print(f"  video: {prod['video_path']} ({prod['duration']:.1f}s)")
    if job.artifacts.get("video_id"):
        print(f"  youtube: https://youtube.com/shorts/{job.artifacts['video_id']}")


def main(argv=None) -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="쇼츠 오케스트레이터")
    p.add_argument("--db", default=Path("output/orchestrator.db"), type=Path)
    p.add_argument("--budget", default=1.5, type=float, help="편당 예산 USD")
    p.add_argument("--auto-publish", action="store_true", help="사람 승인 없이 업로드 (비권장)")
    p.add_argument("--demo", action="store_true", help="API 키 없이 준비된 응답으로 실행 (TTS/렌더는 실제)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new"); s.add_argument("--topic", choices=[t.value for t in TopicArea]); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("run"); s.add_argument("job_id"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("approve"); s.add_argument("job_id"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject"); s.add_argument("job_id"); s.add_argument("reason"); s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("show"); s.add_argument("job_id"); s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
