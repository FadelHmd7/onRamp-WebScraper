"""Store a fetched page: a new version only when its content changed.

Every function runs inside the caller's transaction (``db.session_scope``).
The page row is locked with ``SELECT ... FOR UPDATE`` before deciding, so two
workers that store the same page at the same moment are serialized: the
second one sees the first one's version and decides against it. Two workers
can never both create "version 2".

- ``save_page``          cleaned content: new page -> v1, changed -> v+1,
                         same hash -> nothing new (the page is only touched)
- ``save_not_modified``  a 304 or same-ETag answer: the page is only touched
- ``mark_removed``       404/410 or gone from a full crawl: status "removed";
                         the page and every version stay readable

"Touched" means: last seen now, active again if it had been removed, and
the response's ETag / Last-Modified kept for the next conditional request.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp_scraper.crawler.frontier import Link as FoundLink
from onramp_scraper.processing.changes import Change, Decision, StoredPage, decide
from onramp_scraper.processing.clean import CleanedPage
from onramp_scraper.processing.document import build_document, content_hash
from onramp_scraper.sites import SiteConfig
from onramp_scraper.storage.models import CrawlUrl, DocumentVersion, Link, Page


@dataclass(frozen=True)
class Saved:
    page_id: int
    decision: Decision
    version_id: int | None  # the new document_versions row; None when unchanged


def stored_state(page: Page | None) -> StoredPage | None:
    if page is None:
        return None
    return StoredPage(
        current_version=page.current_version,
        content_hash=page.content_hash,
        etag=page.etag,
        last_modified=page.last_modified,
    )


def load_page(session: Session, url: str) -> StoredPage | None:
    """The stored state before fetching, for conditional headers. Not locked."""
    return stored_state(session.scalars(select(Page).where(Page.url == url)).one_or_none())


def lock_page(session: Session, site_key: str, url: str, seen_at: datetime) -> Page:
    """The page row, created if new, locked until the transaction ends.

    A new row records the crawl's time, not the database clock, so a replayed
    or retried job leaves the same first_seen_at / last_seen_at.
    """
    session.execute(
        insert(Page)
        .values(site_key=site_key, url=url, first_seen_at=seen_at, last_seen_at=seen_at)
        .on_conflict_do_nothing(index_elements=[Page.url])
    )
    page = session.scalars(
        select(Page)
        .where(Page.url == url)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if page.site_key != site_key:
        raise ValueError(f"{url} belongs to site {page.site_key!r}, not {site_key!r}")
    return page


def _touch(page: Page, seen_at: datetime, etag: str | None, last_modified: str | None) -> None:
    page.last_seen_at = max(page.last_seen_at, seen_at)  # a late retry never moves it back
    page.status = "active"
    page.removed_at = None
    page.etag = etag
    page.last_modified = last_modified


def _replayed(session: Session, page: Page, crawl_id: uuid.UUID | None) -> Saved | None:
    """The same crawl already stored this content: a re-delivered job (task D7).

    It reports what the first run did, with the same version row, so the
    crawl's counts do not turn a new page into an "unchanged" one.
    """
    if crawl_id is None or page.current_version is None:
        return None
    current = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.page_id == page.id, DocumentVersion.version == page.current_version
        )
    ).one()
    if current.crawl_id != crawl_id:
        return None
    change = Change.NEW if current.version == 1 else Change.CHANGED
    decision = Decision(change, current.version, "already stored by this crawl")
    return Saved(page.id, decision, current.id)


def save_page(
    session: Session,
    *,
    site: SiteConfig,
    url: str,
    cleaned: CleanedPage,
    fields: dict[str, Any],
    crawled_at: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
    crawl_id: uuid.UUID | None = None,
    fetch_id: int | None = None,
) -> Saved:
    """Decide and store. Raises ``InvalidDocument`` (and the caller rolls back) if invalid."""
    page = lock_page(session, site.key, url, crawled_at)
    decision = decide(stored_state(page), content_hash(cleaned.title, cleaned.blocks, fields))
    _touch(page, crawled_at, etag, last_modified)
    if not decision.is_new_version:
        return _replayed(session, page, crawl_id) or Saved(page.id, decision, None)

    document = build_document(
        cleaned, url=url, site=site, version=decision.version, crawled_at=crawled_at, fields=fields
    )
    row = DocumentVersion(
        page_id=page.id,
        version=document.version,
        content_hash=document.content_hash,
        title=document.title,
        blocks=[block.model_dump() for block in document.blocks],
        fields=document.fields,
        text_content=document.text,
        crawl_id=crawl_id,
        fetch_id=fetch_id,
        crawled_at=crawled_at,
    )
    session.add(row)
    session.flush()  # assigns row.id
    page.current_version = document.version
    page.content_hash = document.content_hash
    return Saved(page.id, decision, row.id)


def save_not_modified(
    session: Session,
    *,
    site: SiteConfig,
    url: str,
    decision: Decision,
    seen_at: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Saved:
    """The server said nothing changed (304, or the same ETag): no new version.

    A 304 may leave out the validators; the stored ones are then kept.
    """
    page = lock_page(session, site.key, url, seen_at)
    if page.current_version is None:
        raise ValueError(f"{url} has no stored version to keep")
    _touch(page, seen_at, etag or page.etag, last_modified or page.last_modified)
    return Saved(page.id, decision, None)


def mark_removed(session: Session, url: str, when: datetime) -> bool:
    """Mark a page removed; its versions stay. False if unknown or already removed."""
    result = session.execute(
        update(Page)
        .where(Page.url == url, Page.status == "active")
        .values(status="removed", removed_at=when)
    )
    return bool(getattr(result, "rowcount", 0))


def stored_links(session: Session, url: str) -> list[FoundLink]:
    """The links of the page's current version, in page order.

    A 304 has no body to read links from; these are the same links, because
    the page has not changed since that version.
    """
    rows = session.execute(
        select(Link.target_url, Link.text, Link.nofollow)
        .join(DocumentVersion, DocumentVersion.id == Link.version_id)
        .join(
            Page,
            (Page.id == DocumentVersion.page_id)
            & (Page.current_version == DocumentVersion.version),
        )
        .where(Page.url == url)
        .order_by(Link.position)
    )
    return [FoundLink(url=t, text=x, nofollow=n) for t, x, n in rows]


def sweep_unreached(session: Session, crawl_id: uuid.UUID, site_key: str, when: datetime) -> int:
    """After a full crawl: the site's active pages it never reached are removed.

    "Never reached" means no row at all in this crawl. A page that failed
    (a 503, the dead-letter queue) was reached and stays active: it may only
    be down for a while. Returns how many pages were marked.
    """
    reached = select(CrawlUrl.url).where(CrawlUrl.crawl_id == crawl_id, CrawlUrl.url == Page.url)
    result = session.execute(
        update(Page)
        .where(Page.site_key == site_key, Page.status == "active", ~reached.exists())
        .values(status="removed", removed_at=when)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))
