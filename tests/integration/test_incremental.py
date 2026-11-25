"""Incremental re-crawls and removal, on the test site's editable pages."""

import uuid

import httpx
import pytest
from pydantic import HttpUrl
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage import crawls
from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import app as testsite
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration

PAGES = ["/editable", "/editable/changelog", "/editable/pricing", "/editable/team"]


@pytest.fixture
def editable(live_testsite: str, pg_engine: Engine, redis_client: Redis) -> LocalCrawl:
    """Crawls of the editable section only: index plus three pages, configured seeds."""
    testsite.state.reset()
    site = local_site(
        live_testsite,
        seeds=[HttpUrl(f"{live_testsite}/editable")],
        include=[r"^/editable(/[a-z0-9-]+)?$"],
    )
    return LocalCrawl(pg_engine, redis_client, site)


def crawl(local: LocalCrawl, mode: str, **kwargs: object) -> uuid.UUID:
    local.results.clear()
    crawl_id = start_crawl(local.context(), "testsite", mode, **kwargs)  # type: ignore[arg-type]
    local.run(workers=2)
    return crawl_id


def outcomes(local: LocalCrawl, base: str) -> dict[str, tuple[str, str]]:
    return {job.url.removeprefix(base): (r.status, r.reason) for job, r in local.results}


def versions(sessions: sessionmaker[Session], base: str) -> dict[str, int]:
    with sessions() as session:
        rows = session.execute(
            text(
                "SELECT p.url, count(v.id) FROM pages p "
                "LEFT JOIN document_versions v ON v.page_id = p.id GROUP BY p.url"
            )
        )
        return {url.removeprefix(base): count for url, count in rows}


def page_status(sessions: sessionmaker[Session], base: str) -> dict[str, str]:
    with sessions() as session:
        rows = session.execute(text("SELECT url, status FROM pages"))
        return {url.removeprefix(base): status for url, status in rows}


def edit(base: str, slug: str, *paragraphs: str) -> None:
    title = slug.capitalize()
    body = {"title": title, "paragraphs": list(paragraphs)}
    httpx.put(f"{base}/_admin/editable/{slug}", json=body).raise_for_status()


def delete(base: str, slug: str) -> None:
    httpx.delete(f"{base}/_admin/editable/{slug}").raise_for_status()


def test_incremental_recrawl_versions_only_the_edited_page(
    editable: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    crawl(editable, "full")
    assert versions(sessions, live_testsite) == dict.fromkeys(PAGES, 1)

    edit(live_testsite, "pricing", "The basic plan now costs $12 per month.")
    crawl_id = crawl(editable, "incremental")

    result = outcomes(editable, live_testsite)
    assert result["/editable/pricing"] == ("fetched", "content hash changed")
    for path in ("/editable", "/editable/changelog", "/editable/team"):
        assert result[path] == ("unchanged", "not modified (304)")  # nothing downloaded
    assert versions(sessions, live_testsite)["/editable/pricing"] == 2  # exactly one new version
    assert sum(versions(sessions, live_testsite).values()) == 5

    with sessions() as session:
        bodies = session.execute(
            text("SELECT outcome, html IS NULL FROM fetches WHERE crawl_id = :c"), {"c": crawl_id}
        ).all()
        old = session.scalar(
            text(
                "SELECT text_content FROM document_versions v JOIN pages p ON p.id = v.page_id "
                "WHERE p.url LIKE '%pricing' AND v.version = 1"
            )
        )
    assert sorted(tuple(b) for b in bodies) == [("not_modified", True)] * 3 + [("ok", False)]
    assert "$10" in old  # the old version is kept


def test_304_pages_still_lead_to_their_links(
    editable: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    """The index answers 304 (no body), yet the crawl reaches every page through it."""
    crawl(editable, "full")
    crawl_id = crawl(editable, "incremental")
    assert outcomes(editable, live_testsite)["/editable"] == ("unchanged", "not modified (304)")
    with sessions() as session:
        done = crawls.progress(session, crawl_id)
    assert done is not None and done.counts["unchanged"] == 4


def test_deleted_page_is_marked_removed_and_keeps_its_versions(
    editable: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    crawl(editable, "full")
    delete(live_testsite, "team")
    # The index changed too and no longer links to it, so ask for the page itself.
    crawl(editable, "incremental", seeds=[f"{live_testsite}/editable/team"])
    status, reason = outcomes(editable, live_testsite)["/editable/team"]
    assert (status, reason) == ("failed", "HTTP 404: page marked removed")
    assert page_status(sessions, live_testsite)["/editable/team"] == "removed"
    assert versions(sessions, live_testsite)["/editable/team"] == 1  # history kept

    # It comes back: active again, and its content is the same, so no new version.
    edit(live_testsite, "team", "We are three engineers.")
    crawl(editable, "full")
    assert page_status(sessions, live_testsite)["/editable/team"] == "active"


def test_full_crawl_marks_pages_it_no_longer_reaches_as_removed(
    editable: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    crawl(editable, "full")
    delete(live_testsite, "changelog")  # the index stops linking to it
    crawl(editable, "full")
    assert "/editable/changelog" not in outcomes(editable, live_testsite)  # never requested
    assert page_status(sessions, live_testsite) == {
        "/editable": "active",
        "/editable/changelog": "removed",
        "/editable/pricing": "active",
        "/editable/team": "active",
    }


def test_custom_seeds_never_mark_pages_removed(
    editable: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    crawl(editable, "full")
    crawl(editable, "full", seeds=[f"{live_testsite}/editable/pricing"])
    assert set(page_status(sessions, live_testsite).values()) == {"active"}


def test_a_crawl_cut_short_by_its_budget_never_marks_pages_removed(
    sessions: sessionmaker[Session], pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> None:
    testsite.state.reset()
    seeds = [HttpUrl(f"{live_testsite}/editable")]
    include = [r"^/editable(/[a-z0-9-]+)?$"]
    whole = LocalCrawl(
        pg_engine, redis_client, local_site(live_testsite, seeds=seeds, include=include)
    )
    crawl(whole, "full")
    small = local_site(live_testsite, seeds=seeds, include=include, max_pages=2)
    crawl(LocalCrawl(pg_engine, redis_client, small), "full")
    assert set(page_status(sessions, live_testsite).values()) == {"active"}
