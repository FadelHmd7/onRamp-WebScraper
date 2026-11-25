"""A7: the public health check reports dependencies, queue length and active workers."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.api.deps import get_sessions
from onramp_scraper.api.routes.health import get_queue_probe, get_redis
from onramp_scraper.queue_stats import QueueDepth
from onramp_scraper.storage import crawls
from onramp_scraper.worker.heartbeat import KEY_PREFIX, Heartbeat

pytestmark = pytest.mark.integration


@pytest.fixture
def health_api(api: TestClient, redis_client: Redis) -> Iterator[TestClient]:
    for key in redis_client.scan_iter(match=KEY_PREFIX + "*"):
        redis_client.delete(key)
    api.app.dependency_overrides[get_redis] = lambda: redis_client  # type: ignore[attr-defined]
    api.app.dependency_overrides[get_queue_probe] = lambda: lambda: QueueDepth(40, 3, 3)  # type: ignore[attr-defined]
    yield api
    for key in redis_client.scan_iter(match=KEY_PREFIX + "*"):
        redis_client.delete(key)


def test_everything_up(
    health_api: TestClient, redis_client: Redis, sessions: sessionmaker[Session]
) -> None:
    busy = Heartbeat(redis_client, "worker-a")
    busy.working_on("http://testsite:8000/items/1")
    Heartbeat(redis_client, "worker-b").beat()
    with sessions.begin() as session:
        crawls.create_crawl(session, "testsite", "full")

    response = health_api.get("/health")  # no token
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert {name: check["ok"] for name, check in body["checks"].items()} == {
        "database": True,
        "redis": True,
        "rabbitmq": True,
    }
    assert body["queue"] == {"ready": 40, "unacked": 3, "backlog": 43, "consumers": 3}
    assert (body["workers"]["active"], body["workers"]["busy"]) == (2, 1)
    assert body["workers"]["list"][0]["current_url"] == "http://testsite:8000/items/1"
    assert body["running_crawls"] == 1


def test_rabbitmq_down_is_degraded_but_the_rest_still_reports(health_api: TestClient) -> None:
    def down() -> QueueDepth:
        raise ConnectionError("rabbitmq:15672 refused")

    health_api.app.dependency_overrides[get_queue_probe] = lambda: down  # type: ignore[attr-defined]
    response = health_api.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["rabbitmq"] == {
        "ok": False,
        "ms": body["checks"]["rabbitmq"]["ms"],
        "error": "ConnectionError: rabbitmq:15672 refused",
    }
    assert body["queue"] is None
    assert body["checks"]["database"]["ok"] and body["workers"] is not None


def test_redis_down_is_degraded(health_api: TestClient) -> None:
    dead = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5)
    health_api.app.dependency_overrides[get_redis] = lambda: dead  # type: ignore[attr-defined]
    body = health_api.get("/health").json()
    assert (body["status"], body["checks"]["redis"]["ok"], body["workers"]) == (
        "degraded",
        False,
        None,
    )


def test_database_down_is_degraded(health_api: TestClient) -> None:
    unreachable = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    health_api.app.dependency_overrides[get_sessions] = lambda: sessionmaker(bind=unreachable)  # type: ignore[attr-defined]
    response = health_api.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert (body["checks"]["database"]["ok"], body["running_crawls"]) == (False, None)
    assert body["checks"]["redis"]["ok"] and body["checks"]["rabbitmq"]["ok"]
