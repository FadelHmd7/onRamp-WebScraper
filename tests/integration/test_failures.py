"""Retries and the dead-letter queue against the test site's failure endpoints."""

import time

import pytest
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.config import Settings
from onramp_scraper.storage.dead_letters import list_dead
from onramp_scraper.worker import pipeline
from onramp_scraper.worker.runs import retry_dead_letter, start_crawl
from onramp_testsite import app as testsite
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration

# Three attempts with short delays, so the tests run in seconds.
FAST = Settings(max_fetch_attempts=3, retry_base_delay=0.05, retry_max_delay=5)


def crawl_one(
    pg_engine: Engine, redis_client: Redis, base: str, path: str
) -> tuple[LocalCrawl, str]:
    site = local_site(base, max_depth=0)  # the seed only, not the pages it links to
    crawl = LocalCrawl(pg_engine, redis_client, site, FAST)
    crawl_id = start_crawl(crawl.context(), site.key, seeds=[base + path])
    crawl.run(workers=2, timeout=60)
    return crawl, str(crawl_id)


def row(sessions: sessionmaker[Session], crawl_id: str) -> tuple[str, int, str | None]:
    with sessions() as session:
        found = session.execute(
            text("SELECT status, attempts, last_error FROM crawl_urls WHERE crawl_id = :c"),
            {"c": crawl_id},
        ).one()
    return found.status, found.attempts, found.last_error


def statuses(crawl: LocalCrawl) -> list[str]:
    return [result.status for _, result in crawl.results]


def test_flaky_page_succeeds_after_retries(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    testsite.state.reset()
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/flaky/1?fail=2")
    assert statuses(crawl) == ["retrying", "retrying", "fetched"]
    assert row(sessions, crawl_id) == ("fetched", 3, None)
    with sessions() as session:
        assert list_dead(session)[1] == 0


def test_always_failing_page_lands_in_the_dead_letter_queue(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/broken/1")
    assert statuses(crawl) == ["retrying", "retrying", "dead"]
    assert row(sessions, crawl_id) == ("dead", 3, "HTTP 503")
    with sessions() as session:
        [dead], total = list_dead(session)
        status = session.scalar(text("SELECT status FROM crawls WHERE id = :c"), {"c": crawl_id})
    assert total == 1
    assert (dead.url, dead.attempts, dead.last_status, dead.last_error) == (
        f"{live_testsite}/broken/1",
        3,
        503,
        "HTTP 503",
    )
    assert status == "finished"  # a dead URL is final: the crawl still ends


def test_429_waits_as_long_as_the_server_asks(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    testsite.state.reset()
    started = time.perf_counter()
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/throttle/1?retry_after=2")
    elapsed = time.perf_counter() - started
    assert statuses(crawl) == ["retrying", "fetched"]
    assert crawl.results[0][1].reason.startswith("HTTP 429; retry in 2.0s")
    assert elapsed >= 2.0  # not sooner than Retry-After, though the backoff alone is 0.05s
    assert row(sessions, crawl_id) == ("fetched", 2, None)


def test_unexpected_error_counts_as_a_failed_attempt(
    sessions: sessionmaker[Session],
    pg_engine: Engine,
    redis_client: Redis,
    live_testsite: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug while cleaning a page must not loop forever: it ends in the dead-letter queue."""

    def broken_cleaner(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cleaner bug")

    monkeypatch.setattr(pipeline, "clean_html", broken_cleaner)
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/items/3")
    assert statuses(crawl) == ["retrying", "retrying", "dead"]
    assert row(sessions, crawl_id) == ("dead", 3, "RuntimeError: cleaner bug")


def test_dead_letter_retry_runs_again_and_leaves_the_queue_on_success(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    testsite.state.reset()
    # Fails 4 times: dead after 3 attempts, then the admin's retry succeeds on its 2nd attempt.
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/flaky/2?fail=4")
    with sessions() as session:
        [dead], _ = list_dead(session)

    assert retry_dead_letter(crawl.context(), dead.id)
    assert not retry_dead_letter(crawl.context(), dead.id)  # not dead any more
    with sessions() as session:
        crawl_status = session.scalar(
            text("SELECT status FROM crawls WHERE id = :c"), {"c": crawl_id}
        )
    assert crawl_status == "running"  # reopened while the URL is queued again

    crawl.results.clear()
    crawl.run(workers=1, timeout=60)
    assert statuses(crawl) == ["retrying", "fetched"]
    assert row(sessions, crawl_id) == ("fetched", 2, None)
    with sessions() as session:
        assert list_dead(session)[1] == 0
        assert list_dead(session, status="resolved")[1] == 1
        crawl_status = session.scalar(
            text("SELECT status FROM crawls WHERE id = :c"), {"c": crawl_id}
        )
    assert crawl_status == "finished"


def test_failed_send_puts_the_dead_letter_back(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    crawl, crawl_id = crawl_one(pg_engine, redis_client, live_testsite, "/broken/2")
    with sessions() as session:
        [dead], _ = list_dead(session)

    def broker_down(jobs: list[pipeline.Job], delay: float) -> None:
        raise ConnectionError("broker unreachable")

    ctx = crawl.context()
    ctx.enqueue = broker_down
    with pytest.raises(ConnectionError):
        retry_dead_letter(ctx, dead.id)
    with sessions() as session:
        assert list_dead(session)[1] == 1  # still there, can be retried later
    assert row(sessions, crawl_id)[0] == "dead"
