"""A6: list the dead-letter queue and retry a job until it leaves the queue."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine

from onramp_scraper.api.deps import crawl_context
from onramp_scraper.config import Settings
from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import app as testsite
from tests.integration.conftest import ADMIN, VIEWER, login
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration

FAST = Settings(max_fetch_attempts=2, retry_base_delay=0.05, retry_max_delay=1)


@pytest.fixture
def failed(
    api: TestClient, pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> Iterator[LocalCrawl]:
    """Two URLs in the dead-letter queue: one that will recover, one that never will."""
    testsite.state.reset()
    crawl = LocalCrawl(pg_engine, redis_client, local_site(live_testsite, max_depth=0), FAST)
    api.app.dependency_overrides[crawl_context] = crawl.context  # type: ignore[attr-defined]
    seeds = [f"{live_testsite}/flaky/7?fail=3", f"{live_testsite}/broken/7"]
    start_crawl(crawl.context(), "testsite", seeds=seeds)
    crawl.run(workers=2)
    yield crawl


def test_admin_sees_the_failed_jobs(
    api: TestClient, failed: LocalCrawl, live_testsite: str
) -> None:
    page = api.get("/dlq", headers=login(api, ADMIN)).json()
    assert page["total"] == 2
    by_url = {item["url"]: item for item in page["items"]}
    broken = by_url[f"{live_testsite}/broken/7"]
    assert (broken["status"], broken["attempts"], broken["last_status"], broken["last_error"]) == (
        "dead",
        2,
        503,
        "HTTP 503",
    )


def test_retry_runs_again_and_leaves_the_queue_on_success(
    api: TestClient, failed: LocalCrawl, live_testsite: str
) -> None:
    admin = login(api, ADMIN)
    flaky = next(
        item for item in api.get("/dlq", headers=admin).json()["items"] if "/flaky/" in item["url"]
    )
    response = api.post(f"/dlq/{flaky['id']}/retry", headers=admin)
    assert response.status_code == 202
    assert response.json()["status"] == "retried"
    crawl = api.get(f"/crawls/{flaky['crawl_id']}", headers=admin).json()
    assert crawl["status"] == "running"  # reopened while the job runs again

    failed.results.clear()
    failed.run(workers=1)  # hit 3 fails, hit 4 succeeds
    assert [r.status for _, r in failed.results] == ["retrying", "fetched"]

    still_dead = api.get("/dlq", headers=admin).json()
    assert [item["url"] for item in still_dead["items"]] == [f"{live_testsite}/broken/7"]
    resolved = api.get("/dlq", params={"status": "resolved"}, headers=admin).json()
    assert [item["id"] for item in resolved["items"]] == [flaky["id"]]
    assert api.get(f"/crawls/{flaky['crawl_id']}", headers=admin).json()["status"] == "finished"


def test_a_job_that_fails_again_returns_to_the_queue(api: TestClient, failed: LocalCrawl) -> None:
    admin = login(api, ADMIN)
    broken = next(
        i for i in api.get("/dlq", headers=admin).json()["items"] if "/broken/" in i["url"]
    )
    assert api.post(f"/dlq/{broken['id']}/retry", headers=admin).status_code == 202
    failed.run(workers=1)
    again = api.get("/dlq", headers=admin).json()
    assert broken["id"] in [item["id"] for item in again["items"]]


def test_retrying_twice_or_an_unknown_job_is_refused(api: TestClient, failed: LocalCrawl) -> None:
    admin = login(api, ADMIN)
    first = api.get("/dlq", headers=admin).json()["items"][0]
    assert api.post(f"/dlq/{first['id']}/retry", headers=admin).status_code == 202
    second = api.post(f"/dlq/{first['id']}/retry", headers=admin)  # a double click
    assert second.status_code == 409
    assert "retried" in second.json()["detail"]
    assert failed.outstanding == 1  # sent once, not twice
    assert api.post("/dlq/999999/retry", headers=admin).status_code == 404


def test_viewers_cannot_see_or_retry_the_queue(api: TestClient, failed: LocalCrawl) -> None:
    viewer = login(api, VIEWER)
    assert api.get("/dlq", headers=viewer).status_code == 403
    assert api.post("/dlq/1/retry", headers=viewer).status_code == 403
