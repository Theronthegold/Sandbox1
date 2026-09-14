"""
pipeline.py

쇼츠 한 편(Job)의 상태 머신.

  QUEUED → RESEARCHING → SCRIPTING ⇄ REVIEWING → DIRECTING → PRODUCING → QA → AWAITING_APPROVAL → PUBLISHING → PUBLISHED
  어느 단계든 예외 → FAILED (error 기록). 단계마다 Job 을 저장하므로 run() 을 다시 부르면 그 단계부터 재개.

안전장치:
  - 편당 예산(per_job_budget): 누적 비용이 넘으면 그 Job 만 FAILED. 전역 breaker 와 별개.
  - writer↔critic 루프 최대 max_review_rounds. 초과 시 마지막 대본으로 진행하고 warnings 에 남김.
  - 업로드 전 사람 승인(approval_required). approve() 호출 전에는 AWAITING_APPROVAL 에서 멈춤.
  - 업로드는 항상 private.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from lib.model_client import ModelClient
from lib.tools.stock import PexelsClient

from .agents import QA, Critic, Director, Publisher, Researcher, Writer
from .producer import ProduceResult, produce
from .schemas import (
    CriticInput,
    CriticOutput,
    DirectorInput,
    DirectorOutput,
    Job,
    JobStatus,
    PublishInput,
    PublishOutput,
    QAInput,
    ResearchInput,
    ResearchOutput,
    Script,
    WriterInput,
    estimate_narration_seconds,
)
from .store import Store

logger = logging.getLogger("orchestrator.pipeline")

Producer = Callable[..., Awaitable[ProduceResult]]
Uploader = Callable[..., Awaitable[str]]


class JobBudgetExceeded(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        store: Store,
        client: ModelClient,
        *,
        stock: Optional[PexelsClient] = None,
        work_root: Path = Path("output/jobs"),
        prompts_dir: Path = Path("prompts"),
        per_job_budget_usd: float = 1.5,
        max_review_rounds: int = 2,
        approval_required: bool = True,
        target_seconds: int = 45,
        producer: Producer = produce,
        uploader: Optional[Uploader] = None,
    ):
        self.store = store
        self.stock = stock
        self.work_root = Path(work_root)
        self.per_job_budget = per_job_budget_usd
        self.max_review_rounds = max_review_rounds
        self.approval_required = approval_required
        self.target_seconds = target_seconds
        self._producer = producer
        self._uploader = uploader or _default_uploader

        self.researcher = Researcher(client, prompts_dir)
        self.writer = Writer(client, prompts_dir)
        self.critic = Critic(client, prompts_dir)
        self.director = Director(client, prompts_dir)
        self.publisher = Publisher(client, prompts_dir)
        self.qa = QA(client, prompts_dir)

    # ------------------------------------------------------------------ public

    async def run(self, job: Job) -> Job:
        """현재 상태에서 멈출 때까지(AWAITING_APPROVAL / PUBLISHED / FAILED) 진행."""
        await self.store.save_job(job)
        try:
            while True:
                if job.status == JobStatus.QUEUED:
                    await self._set(job, JobStatus.RESEARCHING)
                elif job.status == JobStatus.RESEARCHING:
                    await self._research(job)
                    await self._set(job, JobStatus.SCRIPTING)
                elif job.status in (JobStatus.SCRIPTING, JobStatus.REVIEWING):
                    await self._script_and_review(job)
                    await self._set(job, JobStatus.DIRECTING)
                elif job.status == JobStatus.DIRECTING:
                    await self._direct(job)
                    await self._set(job, JobStatus.PRODUCING)
                elif job.status == JobStatus.PRODUCING:
                    await self._produce(job)
                    await self._set(job, JobStatus.QA)
                elif job.status == JobStatus.QA:
                    await self._qa(job)
                    await self._set(job, JobStatus.AWAITING_APPROVAL if self.approval_required else JobStatus.PUBLISHING)
                elif job.status == JobStatus.AWAITING_APPROVAL:
                    await self._event(job, "pipeline", "사람 승인 대기 중. approve() 호출 후 run() 재개")
                    return job
                elif job.status == JobStatus.PUBLISHING:
                    await self._publish(job)
                    await self._set(job, JobStatus.PUBLISHED)
                else:
                    return job
        except Exception as e:  # noqa: BLE001 - 모든 실패를 Job 에 기록
            job.error = f"{type(e).__name__}: {e}"
            job.status = JobStatus.FAILED
            await self.store.save_job(job)
            await self.store.fail_running_steps(job.id, job.error)
            await self._event(job, "pipeline", f"FAILED: {job.error}", level="error")
            logger.exception("job %s failed", job.id)
            return job

    async def approve(self, job: Job) -> Job:
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise ValueError(f"승인할 수 있는 상태가 아닙니다: {job.status.value}")
        await self._set(job, JobStatus.PUBLISHING)
        await self._event(job, "human", "승인됨")
        return job

    async def reject(self, job: Job, reason: str) -> Job:
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise ValueError(f"거절할 수 있는 상태가 아닙니다: {job.status.value}")
        job.error = f"사람이 거절: {reason}"
        await self._set(job, JobStatus.FAILED)
        await self._event(job, "human", job.error, level="warn")
        return job

    # ------------------------------------------------------------------ steps

    async def _research(self, job: Job) -> None:
        step = await self.store.start_step(job.id, "research")
        recent = await self.store.recent_titles(job.topic_area)
        out, cost = await self.researcher.run(ResearchInput(
            topic_area=job.topic_area, topic_label=job.topic_label,
            recent_titles=recent, target_seconds=self.target_seconds,
        ))
        await self._charge(job, cost)
        picked = out.candidates[out.picked_index]
        job.artifacts["research"] = out.model_dump()
        await self.store.finish_step(step, cost_usd=cost, detail=picked.title_idea)
        await self._event(job, "researcher", f"주제 선정: {picked.title_idea} / 후킹: {picked.hook} (${cost:.3f})")

    async def _script_and_review(self, job: Job) -> None:
        research = ResearchOutput.model_validate(job.artifacts["research"])
        candidate = research.candidates[research.picked_index]

        script: Optional[Script] = Script.model_validate(job.artifacts["script"]) if job.artifacts.get("script") else None
        feedback = ""
        if job.artifacts.get("critic"):
            feedback = CriticOutput.model_validate(job.artifacts["critic"]).summary

        while True:
            # ---- writer ----
            job.status = JobStatus.SCRIPTING
            await self.store.save_job(job)
            step = await self.store.start_step(job.id, f"write#{job.review_rounds}")
            script, cost = await self.writer.run(WriterInput(
                topic_label=job.topic_label, candidate=candidate, target_seconds=self.target_seconds,
                feedback=feedback, previous_script=script.model_dump_json() if script else "",
            ))
            await self._charge(job, cost)
            job.artifacts["script"] = script.model_dump()
            est = estimate_narration_seconds(script)
            await self.store.finish_step(step, cost_usd=cost, detail=f"{len(script.scenes)} scenes, ~{est:.0f}s")
            await self._event(job, "writer", f"대본 {len(script.scenes)}씬, 예상 {est:.0f}초: {script.scenes[0].narration} (${cost:.3f})")

            # ---- critic ----
            job.status = JobStatus.REVIEWING
            await self.store.save_job(job)
            step = await self.store.start_step(job.id, f"review#{job.review_rounds}")
            verdict, cost = await self.critic.run(CriticInput(
                topic_label=job.topic_label, candidate=candidate, script=script,
                target_seconds=self.target_seconds, estimated_seconds=round(est, 1),
            ))
            await self._charge(job, cost)
            job.artifacts["critic"] = verdict.model_dump()
            await self.store.finish_step(step, cost_usd=cost, detail=f"score={verdict.score} approve={verdict.approve}")
            await self._event(job, "critic", f"점수 {verdict.score}, 승인 {verdict.approve}, 이슈 {len(verdict.issues)}개 (${cost:.3f})")

            if verdict.approve:
                return
            job.review_rounds += 1
            if job.review_rounds >= self.max_review_rounds:
                job.warnings.append(f"critic 미승인 상태로 진행 (라운드 {job.review_rounds}, 점수 {verdict.score})")
                await self._event(job, "pipeline", job.warnings[-1], level="warn")
                return
            feedback = verdict.summary
            await self._event(job, "pipeline", f"재작성 라운드 {job.review_rounds}: {feedback[:120]}")

    async def _direct(self, job: Job) -> None:
        script = Script.model_validate(job.artifacts["script"])
        step = await self.store.start_step(job.id, "direct")
        out, cost = await self.director.run(DirectorInput(topic_label=job.topic_label, script=script))
        await self._charge(job, cost)
        if len(out.scenes) != len(script.scenes):
            raise ValueError(f"director 씬 수({len(out.scenes)})가 대본({len(script.scenes)})과 다릅니다")
        # 나레이션은 대본 원문으로 강제 (director 가 바꿨더라도)
        for d, s in zip(out.scenes, script.scenes):
            d.narration = s.narration
        job.artifacts["directed"] = out.model_dump()
        await self.store.finish_step(step, cost_usd=cost, detail=f"mood={out.mood}")
        await self._event(job, "director", f"연출 완료, mood={out.mood}, 첫 검색어 '{out.scenes[0].stock_query}' (${cost:.3f})")

        # publisher 는 대본만 있으면 되므로 여기서 같이 실행 (렌더와 독립)
        step = await self.store.start_step(job.id, "publish_meta")
        meta, cost = await self.publisher.run(PublishInput(topic_label=job.topic_label, script=script))
        await self._charge(job, cost)
        job.artifacts["publish_meta"] = meta.model_dump()
        await self.store.finish_step(step, cost_usd=cost, detail=meta.title)
        await self._event(job, "publisher", f"제목: {meta.title} (${cost:.3f})")

    async def _produce(self, job: Job) -> None:
        directed = DirectorOutput.model_validate(job.artifacts["directed"])
        step = await self.store.start_step(job.id, "produce")

        async def on_event(msg: str) -> None:
            await self._event(job, "producer", msg)

        result = await self._producer(directed, self.work_root / job.id, stock=self.stock, on_event=on_event)
        job.artifacts["produce"] = {
            "video_path": str(result.video_path),
            "duration": result.duration,
            "frame_paths": [str(p) for p in result.frame_paths],
            "scene_durations": result.scene_durations,
            "clip_sources": result.clip_sources,
            "music_path": str(result.music_path) if result.music_path else "",
        }
        await self.store.finish_step(step, detail=f"{result.duration:.1f}s")

    async def _qa(self, job: Job) -> None:
        directed = DirectorOutput.model_validate(job.artifacts["directed"])
        prod = job.artifacts["produce"]
        frames = [Path(p) for p in prod["frame_paths"]]
        step = await self.store.start_step(job.id, "qa")
        out, cost = await self.qa.run(
            QAInput(scenes=directed.scenes, duration_seconds=prod["duration"], frame_count=len(frames)),
            images=frames,
        )
        await self._charge(job, cost)
        job.artifacts["qa"] = out.model_dump()
        await self.store.finish_step(step, status="done" if out.passed else "failed", cost_usd=cost,
                                     detail=f"passed={out.passed} issues={len(out.issues)}")
        await self._event(job, "qa", f"통과 {out.passed}, 이슈 {len(out.issues)}개 (${cost:.3f})",
                          level="info" if out.passed else "warn")
        if not out.passed:
            blockers = "; ".join(i.detail for i in out.issues if i.severity == "block")
            raise RuntimeError(f"QA 불통과: {blockers}")

    async def _publish(self, job: Job) -> None:
        meta = PublishOutput.model_validate(job.artifacts["publish_meta"])
        video = Path(job.artifacts["produce"]["video_path"])
        step = await self.store.start_step(job.id, "upload")
        video_id = await self._uploader(video, title=meta.title, description=meta.description, tags=meta.hashtags)
        job.artifacts["video_id"] = video_id
        await self.store.finish_step(step, detail=video_id)
        await self._event(job, "uploader", f"업로드 완료 (private): https://youtube.com/shorts/{video_id}")

    # ------------------------------------------------------------------ helpers

    async def _set(self, job: Job, status: JobStatus) -> None:
        job.status = status
        await self.store.save_job(job)
        await self._event(job, "pipeline", f"→ {status.value}")

    async def _charge(self, job: Job, cost: float) -> None:
        job.cost_usd += cost
        await self.store.save_job(job)
        if job.cost_usd > self.per_job_budget:
            raise JobBudgetExceeded(f"편당 예산 ${self.per_job_budget:.2f} 초과 (누적 ${job.cost_usd:.2f})")

    async def _event(self, job: Job, agent: str, message: str, *, level: str = "info") -> None:
        await self.store.add_event(job.id, agent, message, level=level)


def resume_status(job: Job) -> JobStatus:
    """실패하거나 중단된 Job 의 산출물을 보고 어느 단계부터 다시 돌릴지 결정."""
    a = job.artifacts
    if "video_id" in a:
        return JobStatus.PUBLISHED
    if "qa" in a and (a["qa"] or {}).get("passed"):
        return JobStatus.AWAITING_APPROVAL
    if "produce" in a:
        return JobStatus.QA
    if "directed" in a and "publish_meta" in a:
        return JobStatus.PRODUCING
    if "script" in a and (a.get("critic") or {}).get("approve"):
        return JobStatus.DIRECTING
    if "research" in a:
        return JobStatus.SCRIPTING
    return JobStatus.QUEUED


async def _default_uploader(video: Path, *, title: str, description: str, tags: list) -> str:
    import asyncio

    from lib.tools.youtube import upload_short

    return await asyncio.to_thread(
        upload_short, video, title=title, description=description, tags=tags, privacy_status="private"
    )
