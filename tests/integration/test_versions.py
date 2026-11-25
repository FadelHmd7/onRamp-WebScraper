"""Change detection in the database: versions, kept history, removal, concurrent workers."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.crawler.fetch import FetchResult, Outcome
from onramp_scraper.processing.changes import Change, unchanged_by_headers
from onramp_scraper.processing.clean import CleanedPage, clean_html
from onramp_scraper.processing.document import InvalidDocument
from onramp_scraper.sites import load_sites
from onramp_scraper.storage.models import DocumentVersion, Page
from onramp_scraper.storage.versions import (
    Saved,
    load_page,
    mark_removed,
    save_not_modified,
    save_page,
)

pytestmark = pytest.mark.integration

SITE = load_sites()["testsite"]
URL = "http://testsite:8000/items/1"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def page(body: str, title: str = "Item") -> CleanedPage:
    return clean_html(f"<title>{title}</title><main><h1>{title}</h1><p>{body}</p></main>")


def save(
    sessions: sessionmaker[Session],
    cleaned: CleanedPage,
    at: datetime = T0,
    url: str = URL,
    etag: str | None = None,
) -> Saved:
    with sessions.begin() as session:
        return save_page(
            session, site=SITE, url=url, cleaned=cleaned, fields={}, crawled_at=at, etag=etag
        )


def versions(sessions: sessionmaker[Session], url: str = URL) -> list[tuple[int, str]]:
    with sessions() as session:
        rows = session.execute(
            select(DocumentVersion.version, DocumentVersion.text_content)
            .join(Page, Page.id == DocumentVersion.page_id)
            .where(Page.url == url)
            .order_by(DocumentVersion.version)
        )
        return [(v, t) for v, t in rows]


def stored(sessions: sessionmaker[Session], url: str = URL) -> Page:
    with sessions() as session:
        return session.scalars(select(Page).where(Page.url == url)).one()


def test_new_page_is_version_1(sessions: sessionmaker[Session]) -> None:
    saved = save(sessions, page("first"))
    assert saved.decision.change is Change.NEW and saved.version_id is not None
    assert versions(sessions) == [(1, "Item\nfirst")]
    row = stored(sessions)
    assert (row.current_version, row.status, row.content_hash) == (1, "active", row.content_hash)


def test_same_content_adds_no_version(sessions: sessionmaker[Session]) -> None:
    save(sessions, page("same"), T0)
    saved = save(sessions, page("same"), T0 + timedelta(days=1))
    assert (saved.decision.change, saved.decision.reason) == (
        Change.UNCHANGED,
        "same content hash",
    )
    assert saved.version_id is None
    assert versions(sessions) == [(1, "Item\nsame")]
    assert stored(sessions).last_seen_at == T0 + timedelta(days=1)  # but we saw it again


def test_seen_times_are_the_crawl_times(sessions: sessionmaker[Session]) -> None:
    """Not the database clock: a replayed or late job gives the same times."""
    save(sessions, page("x"), T0 + timedelta(days=1))
    save(sessions, page("x"), T0)  # a retry of an older job finishes last
    row = stored(sessions)
    assert row.first_seen_at == T0 + timedelta(days=1)
    assert row.last_seen_at == T0 + timedelta(days=1)  # never moved back


def test_changed_content_is_the_next_version_and_old_ones_are_kept(
    sessions: sessionmaker[Session],
) -> None:
    save(sessions, page("A"), T0)
    save(sessions, page("B"), T0 + timedelta(days=1))
    third = save(sessions, page("A"), T0 + timedelta(days=2))  # back to A: still a new version
    assert third.decision.change is Change.CHANGED
    assert versions(sessions) == [(1, "Item\nA"), (2, "Item\nB"), (3, "Item\nA")]
    assert stored(sessions).current_version == 3


def test_304_and_same_etag_only_touch_the_page(sessions: sessionmaker[Session]) -> None:
    save(sessions, page("v1"), T0, etag='"e1"')
    with sessions() as session:
        state = load_page(session, URL)
    assert state is not None and state.etag == '"e1"'

    not_modified = FetchResult(url=URL, outcome=Outcome.NOT_MODIFIED, status=304)
    decision = unchanged_by_headers(state, not_modified)
    assert decision is not None
    with sessions.begin() as session:
        save_not_modified(
            session, site=SITE, url=URL, decision=decision, seen_at=T0 + timedelta(hours=1)
        )
    assert versions(sessions) == [(1, "Item\nv1")]
    row = stored(sessions)
    assert row.etag == '"e1"'  # a 304 without validators keeps the stored ones
    assert row.last_seen_at == T0 + timedelta(hours=1)


def test_removed_page_keeps_its_versions_and_can_come_back(
    sessions: sessionmaker[Session],
) -> None:
    save(sessions, page("A"), T0)
    save(sessions, page("B"), T0 + timedelta(days=1))
    with sessions.begin() as session:
        assert mark_removed(session, URL, T0 + timedelta(days=2))
    with sessions.begin() as session:
        assert not mark_removed(session, URL, T0 + timedelta(days=3))  # already removed
        assert not mark_removed(session, "http://testsite:8000/unknown", T0)
    row = stored(sessions)
    assert (row.status, row.removed_at) == ("removed", T0 + timedelta(days=2))
    assert len(versions(sessions)) == 2  # nothing deleted

    back = save(sessions, page("B"), T0 + timedelta(days=4))  # it reappears unchanged
    assert back.decision.change is Change.UNCHANGED
    row = stored(sessions)
    assert (row.status, row.removed_at) == ("active", None)


def test_invalid_document_stores_nothing(sessions: sessionmaker[Session]) -> None:
    empty = clean_html("<title></title><main></main>")
    with pytest.raises(InvalidDocument):
        save(sessions, empty)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Page)) == 0


def test_a_page_cannot_move_to_another_site(sessions: sessionmaker[Session]) -> None:
    save(sessions, page("x"))
    other = load_sites()["books-toscrape"]
    with pytest.raises(ValueError, match="belongs to site 'testsite'"), sessions.begin() as session:
        save_page(session, site=other, url=URL, cleaned=page("x"), fields={}, crawled_at=T0)


@pytest.mark.parametrize("same_content", [True, False], ids=["same content", "different"])
def test_concurrent_workers_never_duplicate_a_version(
    sessions: sessionmaker[Session], same_content: bool
) -> None:
    """Eight workers store the same new page at once: the row lock serializes them."""
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        body = "same" if same_content else f"edit {n}"
        try:
            start.wait()
            save(sessions, page(body), T0 + timedelta(seconds=n))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    numbers = [v for v, _ in versions(sessions)]
    assert numbers == ([1] if same_content else list(range(1, 9)))
    assert stored(sessions).current_version == numbers[-1]
