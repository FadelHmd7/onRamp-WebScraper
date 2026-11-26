"""A whole crawl of the test site: real HTTP, PostgreSQL and Redis, four workers."""

from collections import Counter
from dataclasses import replace

import pytest
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.worker.pipeline import Job, process
from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import content
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration

CATALOG_AND_ITEMS = content.CATALOG_PAGES + content.ITEM_COUNT  # 630


def scalar(sessions: sessionmaker[Session], sql: str, **params: object) -> object:
    with sessions() as session:
        return session.scalar(text(sql), params)


def test_four_workers_store_every_page_once(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    site = local_site(live_testsite, max_pages=1000, max_depth=40)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    crawl_id = start_crawl(crawl.context(), site.key)
    # Generous: on a laptop, Docker Desktop's port forwarding makes each of the
    # ~20 database round trips per page slow (37-110 s seen); CI takes far less.
    crawl.run(workers=4, timeout=300)

    statuses = Counter(job_result.status for _, job_result in crawl.results)
    assert statuses["fetched"] >= CATALOG_AND_ITEMS
    assert statuses["failed"] == 0
    assert "duplicate" not in statuses  # no URL was handed out twice

    # Every page once: one version, one fetch, one crawl_urls row each.
    pages = scalar(sessions, "SELECT count(*) FROM pages")
    assert pages == statuses["fetched"]
    assert scalar(sessions, "SELECT count(*) FROM document_versions") == pages
    assert scalar(sessions, "SELECT count(*) FROM fetches WHERE crawl_id = :c", c=crawl_id) == pages
    assert scalar(sessions, "SELECT max(version) FROM document_versions") == 1
    rows = scalar(sessions, "SELECT count(*) FROM crawl_urls WHERE crawl_id = :c", c=crawl_id)
    assert rows == len(crawl.results)
    assert scalar(sessions, "SELECT count(*) FROM crawl_urls WHERE status = 'queued'") == 0

    # The catalog and every item are there, with their fields.
    items = scalar(sessions, "SELECT count(*) FROM pages WHERE url LIKE '%/items/%'")
    assert items == content.ITEM_COUNT
    sku = scalar(
        sessions,
        "SELECT v.fields->>'sku' FROM document_versions v JOIN pages p ON p.id = v.page_id "
        "WHERE p.url = :u",
        u=f"{live_testsite}/items/1",
    )
    assert sku == content.item(1).sku


def test_page_budget_and_depth_limit_hold(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    site = local_site(live_testsite, max_pages=50, max_depth=40)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    crawl_id = start_crawl(crawl.context(), site.key)
    crawl.run(workers=4)
    assert len(crawl.results) == 50
    assert scalar(sessions, "SELECT count(*) FROM crawl_urls WHERE crawl_id = :c", c=crawl_id) == 50

    shallow = local_site(live_testsite, max_pages=1000, max_depth=0)
    crawl = LocalCrawl(pg_engine, redis_client, shallow)
    start_crawl(crawl.context(), shallow.key)
    crawl.run(workers=2)
    assert [job.url for job, _ in crawl.results] == [f"{live_testsite}/catalog?page=1"]


def test_robots_disallowed_seed_is_skipped_without_a_request(
    pg_engine: Engine, redis_client: Redis, live_testsite: str, sessions: sessionmaker[Session]
) -> None:
    site = local_site(live_testsite, include=["^/private/", "^/catalog"])
    crawl = LocalCrawl(pg_engine, redis_client, site)
    start_crawl(crawl.context(), site.key, seeds=[f"{live_testsite}/private/secret"])
    crawl.run(workers=1)
    [(_, result)] = crawl.results
    assert (result.status, result.reason) == ("skipped", "disallowed by robots.txt")
    assert scalar(sessions, "SELECT count(*) FROM pages") == 0


def test_seeds_must_be_in_scope(pg_engine: Engine, redis_client: Redis, live_testsite: str) -> None:
    site = local_site(live_testsite)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    with pytest.raises(ValueError, match="seeds outside site"):
        start_crawl(crawl.context(), site.key, seeds=["https://example.com/"])
    with pytest.raises(ValueError, match="unknown site"):
        start_crawl(crawl.context(), "nope")


def test_redelivered_job_resends_links_its_crashed_run_accepted(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    """The worker dies after the frontier accepted a page's links, before recording them."""
    site = local_site(live_testsite, max_pages=1000, max_depth=1)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    ctx = crawl.context()
    crawl_id = start_crawl(ctx, site.key)
    seed = crawl.queue.get_nowait()
    crawl.outstanding -= 1

    def die(jobs: list[Job], delay: float) -> None:
        raise ConnectionError("worker killed while sending")

    with pytest.raises(ConnectionError):
        process(seed, replace(ctx, enqueue=die))
    accepted = int(redis_client.get(f"crawl:{crawl_id}:accepted") or 0)
    assert accepted > 1  # the links were taken in Redis...
    assert (
        scalar(sessions, "SELECT count(*) FROM crawl_urls WHERE depth = 1") == 0
    )  # ...not recorded
    assert scalar(sessions, "SELECT status FROM crawl_urls WHERE depth = 0") == "queued"

    # Without the redelivery flag the links would be lost: the frontier says "seen".
    assert process(seed, replace(ctx, enqueue=lambda jobs, delay: None)).children == []
    # RabbitMQ marks the message redelivered; that run sends the orphans again.
    with sessions.begin() as session:
        session.execute(text("UPDATE crawl_urls SET status = 'queued' WHERE depth = 0"))
    result = process(seed, ctx, redelivered=True)
    assert len(result.children) == accepted - 1
    crawl.run(workers=4)
    assert scalar(sessions, "SELECT count(*) FROM crawl_urls WHERE status = 'fetched'") == accepted
