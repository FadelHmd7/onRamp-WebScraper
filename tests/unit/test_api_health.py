"""/health without real services: fakes for the database, Redis and RabbitMQ.

The same checks against real PostgreSQL and Redis are in
tests/integration/test_api_health.py.
"""

from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from onramp_scraper.api.app import create_app
from onramp_scraper.api.deps import get_session
from onramp_scraper.api.routes.health import get_queue_probe, get_redis
from onramp_scraper.queue_stats import QueueDepth
from onramp_scraper.worker.heartbeat import Heartbeat


class FakeSession:
    def __init__(self, running: int | Exception) -> None:
        self.running = running

    def scalar(self, *_: Any) -> int:
        if isinstance(self.running, Exception):
            raise self.running
        return self.running


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def client(session: FakeSession, redis: Any, probe: Any) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_queue_probe] = lambda: probe
    return TestClient(app)


def test_health_is_public_and_ok(redis: fakeredis.FakeRedis) -> None:
    Heartbeat(redis, "w1").beat()
    api = client(FakeSession(running=2), redis, lambda: QueueDepth(5, 1, 1))
    response = api.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert (body["queue"]["backlog"], body["workers"]["active"], body["running_crawls"]) == (
        6,
        1,
        2,
    )


def test_any_dependency_down_is_503_degraded(redis: fakeredis.FakeRedis) -> None:
    def rabbit_down() -> QueueDepth:
        raise ConnectionError("refused")

    api = client(FakeSession(running=ConnectionError("db down")), redis, rabbit_down)
    response = api.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert {k: v["ok"] for k, v in body["checks"].items()} == {
        "database": False,
        "redis": True,
        "rabbitmq": False,
    }
    assert body["checks"]["database"]["error"] == "ConnectionError: db down"


def test_the_module_level_app_serves_health() -> None:
    """``onramp_scraper.api.app:app`` is what uvicorn runs."""
    from onramp_scraper.api.app import app

    assert any(path == "/health" for path in app.openapi()["paths"])
