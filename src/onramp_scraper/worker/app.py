"""Celery application shared by every worker container.

The settings below are what make a killed worker safe:

- ``task_acks_late``: a job is acknowledged only after it finishes, so a job
  whose worker dies is still unacknowledged in RabbitMQ.
- ``task_reject_on_worker_lost``: if the worker process dies, the job goes
  back to the queue instead of being marked failed.
- ``worker_prefetch_multiplier = 1``: a worker holds one job at a time, so a
  crash loses at most that one job, and RabbitMQ redelivers it.

The crawl work itself is in ``worker/pipeline.py``; this module only adapts
it to Celery.
"""

from functools import lru_cache
from typing import Any

from celery import Celery, Task

from onramp_scraper.config import get_settings
from onramp_scraper.sites import load_sites
from onramp_scraper.storage.db import get_engine
from onramp_scraper.worker.pipeline import CrawlContext, Job, process
from onramp_scraper.worker.runtime import build_context

CRAWL_QUEUE = "crawl"

app = Celery("onramp_scraper", broker=get_settings().broker_url)
app.conf.update(
    task_default_queue=CRAWL_QUEUE,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,  # results live in PostgreSQL, not in a Celery backend
)


def send_jobs(jobs: list[Job], delay: float = 0.0) -> None:
    for job in jobs:
        crawl_page.apply_async(kwargs={"job": job.to_message()}, countdown=max(delay, 0.0))


@lru_cache
def context() -> CrawlContext:
    """Built on the first job, after Celery has forked the worker process."""
    from redis import Redis

    settings = get_settings()
    return build_context(
        settings,
        load_sites(),
        get_engine(),
        Redis.from_url(settings.redis_url),
        send_jobs,
    )


@app.task(name="crawl.page", bind=True)
def crawl_page(self: Task[Any, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Crawl one URL. RabbitMQ marks a message redelivered when its worker died."""
    redelivered = bool((self.request.delivery_info or {}).get("redelivered"))
    result = process(Job.from_message(job), context(), redelivered=redelivered)
    return {"status": result.status, "reason": result.reason, "children": len(result.children)}


@app.task(name="worker.ping")
def ping() -> str:
    """Proves a worker consumes from the queue (used by CI's smoke test)."""
    return "pong"
