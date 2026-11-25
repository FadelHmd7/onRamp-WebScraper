"""Idempotent writes: running the same job twice leaves the same rows."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.crawler.fetch import FetchResult, Outcome
from onramp_scraper.crawler.frontier import extract_links
from onramp_scraper.processing.changes import Change, StoredPage, unchanged_by_headers
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.fields import extract_fields
from onramp_scraper.sites import load_sites
from onramp_scraper.storage.models import Crawl
from onramp_scraper.storage.versions import Saved
from onramp_scraper.storage.writes import store_not_modified, store_page
from onramp_testsite import content

pytestmark = pytest.mark.integration

SITE = load_sites()["testsite"]
URL = "http://testsite:8000/items/7"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

SNAPSHOT = {
    "pages": "SELECT * FROM pages ORDER BY id",
    "fetches": "SELECT * FROM fetches ORDER BY id",
    "document_versions": "SELECT * FROM document_versions ORDER BY id",
    "links": "SELECT * FROM links ORDER BY version_id, position",
}


def snapshot(sessions: sessionmaker[Session]) -> dict[str, list[tuple[Any, ...]]]:
    with sessions() as session:
        return {
            table: [tuple(row) for row in session.execute(text(query))]
            for table, query in SNAPSHOT.items()
        }


def new_crawl(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions.begin() as session:
        crawl = Crawl(site_key=SITE.key, mode="full")
        session.add(crawl)
        session.flush()
        return crawl.id


def fetched(html: str, at: datetime = T0, etag: str | None = '"e1"') -> FetchResult:
    headers = {"content-type": "text/html; charset=utf-8"}
    if etag:
        headers["etag"] = etag
    return FetchResult(
        url=URL, outcome=Outcome.OK, status=200, body=html, headers=headers, fetched_at=at
    )


def item_html(summary_extra: str = "") -> str:
    item = content.item(7)
    return content.layout("x", content.item_body(item)).replace(
        item.summary, item.summary + summary_extra
    )


def store(sessions: sessionmaker[Session], crawl_id: uuid.UUID, fetch: FetchResult) -> Saved:
    """One crawl job's writes, in one transaction, as the worker will run them."""
    html = fetch.body or ""
    with sessions.begin() as session:
        return store_page(
            session,
            site=SITE,
            crawl_id=crawl_id,
            fetch=fetch,
            cleaned=clean_html(html),
            fields=extract_fields(html, SITE.fields),
            links=extract_links(html, fetch.url),
        )


# ---------------------------------------------------------------- the "done when" test


def test_saving_the_same_result_twice_leaves_the_same_rows(
    sessions: sessionmaker[Session],
) -> None:
    crawl = new_crawl(sessions)
    fetch = fetched(item_html())
    first = store(sessions, crawl, fetch)
    rows = snapshot(sessions)
    assert [len(rows[t]) for t in SNAPSHOT] == [1, 1, 1, len(extract_links(fetch.body or "", URL))]
    assert len(rows["links"]) > 0

    again = store(sessions, crawl, fetch)  # the job is delivered a second time
    assert snapshot(sessions) == rows
    # ... and reports what the first run did, not "unchanged".
    assert again.decision.change is first.decision.change is Change.NEW
    assert again.version_id == first.version_id
    assert again.decision.reason == "already stored by this crawl"


def test_replay_after_a_change_reports_the_change(sessions: sessionmaker[Session]) -> None:
    store(sessions, new_crawl(sessions), fetched(item_html()))
    crawl = new_crawl(sessions)
    edited = fetched(item_html(" Edited."), T0 + timedelta(days=1), etag='"e2"')
    first = store(sessions, crawl, edited)
    rows = snapshot(sessions)
    again = store(sessions, crawl, edited)
    assert snapshot(sessions) == rows
    assert (first.decision.change, first.decision.version) == (Change.CHANGED, 2)
    assert (again.decision.change, again.version_id) == (Change.CHANGED, first.version_id)


def test_a_replay_that_finishes_with_a_later_fetch_time_changes_nothing_else(
    sessions: sessionmaker[Session],
) -> None:
    """The re-delivered job fetched again a moment later: same content, same rows."""
    crawl = new_crawl(sessions)
    store(sessions, crawl, fetched(item_html(), T0))
    store(sessions, crawl, fetched(item_html(), T0 + timedelta(seconds=30)))
    rows = snapshot(sessions)
    assert (len(rows["fetches"]), len(rows["document_versions"])) == (1, 1)
    with sessions() as session:
        fetch_time = session.scalar(text("SELECT fetched_at FROM fetches"))
        version_time = session.scalar(text("SELECT crawled_at FROM document_versions"))
    assert fetch_time == T0 + timedelta(seconds=30)  # the raw fetch is the latest one
    assert version_time == T0  # the version keeps the time it was first stored


def test_concurrent_deliveries_of_one_job_leave_one_set_of_rows(
    sessions: sessionmaker[Session],
) -> None:
    crawl = new_crawl(sessions)
    fetch = fetched(item_html())
    start = threading.Barrier(6)
    results: list[Saved] = []
    errors: list[BaseException] = []

    def deliver() -> None:
        try:
            start.wait()
            results.append(store(sessions, crawl, fetch))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    rows = snapshot(sessions)
    assert (len(rows["pages"]), len(rows["fetches"]), len(rows["document_versions"])) == (1, 1, 1)
    assert {r.version_id for r in results} == {rows["document_versions"][0][0]}
    assert {r.decision.change for r in results} == {Change.NEW}


# ---------------------------------------------------------------- across crawls


def test_next_crawl_of_an_unchanged_page_adds_only_its_fetch(
    sessions: sessionmaker[Session],
) -> None:
    store(sessions, new_crawl(sessions), fetched(item_html()))
    before = snapshot(sessions)
    later = store(sessions, new_crawl(sessions), fetched(item_html(), T0 + timedelta(days=1)))
    after = snapshot(sessions)
    assert later.decision.change is Change.UNCHANGED and later.version_id is None
    assert len(after["fetches"]) == 2  # raw data is kept per crawl
    assert after["document_versions"] == before["document_versions"]
    assert after["links"] == before["links"]


def test_not_modified_twice_records_one_fetch(sessions: sessionmaker[Session]) -> None:
    store(sessions, new_crawl(sessions), fetched(item_html()))
    crawl = new_crawl(sessions)
    not_modified = FetchResult(
        url=URL, outcome=Outcome.NOT_MODIFIED, status=304, fetched_at=T0 + timedelta(days=1)
    )
    decision = unchanged_by_headers(StoredPage(current_version=1, etag='"e1"'), not_modified)
    assert decision is not None
    for _ in range(2):
        with sessions.begin() as session:
            store_not_modified(
                session, site=SITE, crawl_id=crawl, fetch=not_modified, decision=decision
            )
    with sessions() as session:
        rows = session.execute(
            text("SELECT status_code, outcome, html FROM fetches WHERE crawl_id = :c"),
            {"c": crawl},
        ).all()
        etag = session.scalar(text("SELECT etag FROM pages"))
    assert [tuple(r) for r in rows] == [(304, "not_modified", None)]
    assert etag == '"e1"'


# ---------------------------------------------------------------- what is stored


def test_raw_fetch_keeps_html_status_time_and_headers(sessions: sessionmaker[Session]) -> None:
    fetch = fetched(item_html())
    store(sessions, new_crawl(sessions), fetch)
    with sessions() as session:
        row = session.execute(
            text("SELECT html, status_code, fetched_at, headers, outcome FROM fetches")
        ).one()
    assert row.html == fetch.body
    assert (row.status_code, row.fetched_at, row.outcome) == (200, T0, "ok")
    assert row.headers["etag"] == '"e1"'


def test_outbound_links_are_queryable(sessions: sessionmaker[Session]) -> None:
    """Source page, target URL and link text of the current version, in page order."""
    html = item_html()
    store(sessions, new_crawl(sessions), fetched(html))
    with sessions() as session:
        rows = session.execute(
            text(
                "SELECT p.url, l.target_url, l.text FROM links l "
                "JOIN document_versions v ON v.id = l.version_id "
                "JOIN pages p ON p.id = v.page_id AND v.version = p.current_version "
                "ORDER BY l.position"
            )
        ).all()
    expected = [(URL, link.url, link.text) for link in extract_links(html, URL)]
    assert [tuple(r) for r in rows] == expected
