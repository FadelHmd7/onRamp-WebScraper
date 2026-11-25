"""The public health check: dependencies, queue length and active workers.

The only route that needs no token. It answers 200 when PostgreSQL, Redis
and RabbitMQ all respond, and 503 when any of them does not, so the same URL
serves Docker's healthcheck, monitoring and the dashboard.
"""

import time
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import func, select

from onramp_scraper import __version__
from onramp_scraper.api.deps import SessionDep
from onramp_scraper.config import get_settings
from onramp_scraper.queue_stats import QueueDepth, queue_depth
from onramp_scraper.storage.models import Crawl
from onramp_scraper.worker.heartbeat import active_workers

router = APIRouter(tags=["health"])


@lru_cache
def get_redis() -> Redis:
    # Short timeouts: a hanging Redis must not hang the health check.
    return Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


def get_queue_probe() -> Callable[[], QueueDepth]:
    settings = get_settings()
    return lambda: queue_depth(settings)


class Check(BaseModel):
    ok: bool
    ms: float = Field(description="How long the check took")
    error: str | None = None


class Queue(BaseModel):
    ready: int = Field(description="Jobs waiting for a worker")
    unacked: int = Field(description="Jobs held by workers: running, or retries waiting")
    backlog: int
    consumers: int


class Worker(BaseModel):
    name: str
    current_url: str | None
    processed: int
    last_beat: str


class Workers(BaseModel):
    active: int = Field(description="Workers with a live heartbeat")
    busy: int
    list: list[Worker]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, Check]
    queue: Queue | None
    workers: Workers | None
    running_crawls: int | None


def _timed(probe: Callable[[], Any]) -> tuple[Check, Any]:
    started = time.perf_counter()
    try:
        value = probe()
    except Exception as exc:  # any failure of a dependency is a failed check
        ms = round((time.perf_counter() - started) * 1000, 1)
        return Check(ok=False, ms=ms, error=f"{type(exc).__name__}: {exc}"[:300]), None
    return Check(ok=True, ms=round((time.perf_counter() - started) * 1000, 1)), value


@router.get(
    "/health",
    response_model=Health,
    responses={503: {"model": Health, "description": "A dependency is down"}},
)
def health(
    response: Response,
    session: SessionDep,
    redis: Annotated[Redis, Depends(get_redis)],
    probe: Annotated[Callable[[], QueueDepth], Depends(get_queue_probe)],
) -> Health:
    """Public: is everything up, how long is the queue, how many workers are alive."""
    # Counting running crawls is the database check: it needs a working connection.
    running_query = select(func.count()).select_from(Crawl).where(Crawl.status == "running")
    database, running = _timed(lambda: session.scalar(running_query))
    redis_check, workers = _timed(lambda: active_workers(redis))
    rabbit_check, depth = _timed(probe)

    healthy = database.ok and redis_check.ok and rabbit_check.ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Health(
        status="ok" if healthy else "degraded",
        version=__version__,
        checks={"database": database, "redis": redis_check, "rabbitmq": rabbit_check},
        queue=Queue(
            ready=depth.ready,
            unacked=depth.unacked,
            backlog=depth.backlog,
            consumers=depth.consumers,
        )
        if depth is not None
        else None,
        workers=Workers(
            active=len(workers),
            busy=sum(1 for w in workers if w.get("current_url")),
            list=[
                Worker(
                    name=w["name"],
                    current_url=w.get("current_url"),
                    processed=w.get("processed", 0),
                    last_beat=w.get("last_beat", ""),
                )
                for w in workers
            ],
        )
        if workers is not None
        else None,
        running_crawls=running,
    )
