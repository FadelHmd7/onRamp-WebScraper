"""Crawl runs: progress counts from the database, and finishing exactly once."""

import threading
import uuid
from collections import Counter

import pytest
from redis import Redis
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage import crawls
from onramp_scraper.storage.models import Crawl
from onramp_scraper.worker.runs import start_crawl
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration


def progress(sessions: sessionmaker[Session], crawl_id: uuid.UUID) -> crawls.CrawlProgress:
    with sessions() as session:
        found = crawls.progress(session, crawl_id)
    assert found is not None
    return found


def test_progress_counts_match_the_database_after_a_run(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    site = local_site(live_testsite, max_pages=120, max_depth=40)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    crawl_id = start_crawl(crawl.context(), site.key)
    assert progress(sessions, crawl_id).status == "running"

    seen_while_running: list[str] = []
    crawl.before_job = lambda job: seen_while_running.append(progress(sessions, crawl_id).status)
    crawl.run(workers=4)

    done = progress(sessions, crawl_id)
    by_status = Counter(result.status for _, result in crawl.results)
    assert {k: v for k, v in done.counts.items() if v} == dict(by_status)
    assert (done.total, done.done, done.counts["queued"], done.errors) == (120, 120, 0, 0)
    with sessions() as session:
        rows = session.scalar(
            text("SELECT count(*) FROM crawl_urls WHERE crawl_id = :c"), {"c": crawl_id}
        )
    assert rows == done.total
    # Finished only after the last job: every job saw the crawl still running.
    assert set(seen_while_running) == {"running"}
    assert done.status == "finished"
    assert done.started_at is not None and done.finished_at is not None
    assert done.finished_at >= done.started_at


def test_every_outcome_is_counted(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    site = local_site(live_testsite, include=["^/items/", "^/private/"], max_depth=0)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    crawl_id = start_crawl(
        crawl.context(),
        site.key,
        seeds=[
            f"{live_testsite}/items/1",  # stored
            f"{live_testsite}/items/99999",  # 404: permanent failure
            f"{live_testsite}/private/x",  # robots.txt: skipped
        ],
    )
    crawl.run(workers=2)
    counts = progress(sessions, crawl_id).counts
    assert (counts["fetched"], counts["failed"], counts["skipped"]) == (1, 1, 1)

    # A second crawl of the same page: unchanged, no new version.
    again = start_crawl(crawl.context(), site.key, seeds=[f"{live_testsite}/items/1"])
    crawl.run(workers=1)
    assert progress(sessions, again).counts["unchanged"] == 1
    with sessions() as session:
        error = session.scalar(
            text("SELECT last_error FROM crawl_urls WHERE url LIKE '%99999'"),
        )
    assert error == "HTTP 404"


def test_finishing_happens_exactly_once_under_a_race(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        crawl_id = crawls.create_crawl(session, "testsite", "full").id
        crawls.set_url_status(
            session, crawl_id, "http://testsite:8000/", depth=0, status="fetched", attempts=1
        )
    start = threading.Barrier(8)
    finished: list[bool] = []

    def last_job_done() -> None:
        start.wait()
        with sessions.begin() as session:
            finished.append(crawls.finish_if_done(session, crawl_id))

    threads = [threading.Thread(target=last_job_done) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert finished.count(True) == 1


def test_a_queued_url_keeps_the_crawl_running_and_reopen_restarts_it(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        crawl_id = crawls.create_crawl(session, "testsite", "full").id
        crawls.record_queued(session, crawl_id, ["http://testsite:8000/a"], 0)
    with sessions.begin() as session:
        assert not crawls.finish_if_done(session, crawl_id)
        crawls.set_url_status(
            session, crawl_id, "http://testsite:8000/a", depth=0, status="dead", attempts=5
        )
    with sessions.begin() as session:
        assert crawls.finish_if_done(session, crawl_id)
        crawls.reopen(session, crawl_id)
    with sessions() as session:
        crawl = session.scalars(select(Crawl).where(Crawl.id == crawl_id)).one()
    assert (crawl.status, crawl.finished_at) == ("running", None)


def test_jobs_of_a_cancelled_crawl_do_nothing(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    site = local_site(live_testsite, max_pages=10)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    crawl_id = start_crawl(crawl.context(), site.key)
    with sessions.begin() as session:
        session.execute(text("UPDATE crawls SET status = 'failed' WHERE id = :c"), {"c": crawl_id})
    crawl.run(workers=1)
    assert [result.status for _, result in crawl.results] == ["cancelled"]
