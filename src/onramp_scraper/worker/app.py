"""Celery application shared by every worker container.

The settings below are what make a killed worker safe:

- ``task_acks_late``: a job is acknowledged only after it finishes, so a job
  whose worker dies is still unacknowledged in RabbitMQ.
- ``task_reject_on_worker_lost``: if the worker process dies, the job goes
  back to the queue instead of being marked failed.
- ``worker_prefetch_multiplier = 1``: a worker holds one job at a time, so a
  crash loses at most that one job, and RabbitMQ redelivers it.
"""

from celery import Celery

from onramp_scraper.config import get_settings

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
)


@app.task(name="worker.ping")
def ping() -> str:
    """E1 stub task: proves a worker consumes from the queue."""
    return "pong"
