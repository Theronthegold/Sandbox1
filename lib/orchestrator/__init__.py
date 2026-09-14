"""
lib.orchestrator

쇼츠 한 편을 만드는 상태 머신과 에이전트/도구 연결.

    from lib.orchestrator import Pipeline, Store, Job, TopicArea

    async with Store() as store:
        pipeline = Pipeline(store, get_client(), stock=PexelsClient())
        job = Job(topic_area=TopicArea.money_psychology)
        job = await pipeline.run(job)          # → AWAITING_APPROVAL
        await pipeline.approve(job)
        job = await pipeline.run(job)          # → PUBLISHED
"""

from .pipeline import JobBudgetExceeded, Pipeline
from .schemas import Job, JobStatus, TopicArea
from .store import Store

__all__ = ["Pipeline", "JobBudgetExceeded", "Store", "Job", "JobStatus", "TopicArea"]
