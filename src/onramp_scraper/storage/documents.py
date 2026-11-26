"""Read queries for the API: documents (a page and its current version) and raw fetches.

A document's id is its page's id. It stays the same across versions; the
document shows the page's current version, and ``/documents/{id}/versions``
lists them all.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from onramp_scraper.storage.models import DocumentVersion, Fetch, Link, Page


@dataclass(frozen=True)
class DocumentRow:
    page: Page
    version: DocumentVersion


def _current() -> Select[Page, DocumentVersion]:
    """Pages joined to their current version (pages never stored have none)."""
    return select(Page, DocumentVersion).join(
        DocumentVersion,
        (DocumentVersion.page_id == Page.id) & (DocumentVersion.version == Page.current_version),
    )


def list_documents(
    session: Session,
    *,
    site_key: str | None = None,
    status: str | None = None,
    changed_after: datetime | None = None,
    changed_before: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[DocumentRow], int]:
    """A page of documents, most recently changed first, and the total."""
    query = _current()
    if site_key is not None:
        query = query.where(Page.site_key == site_key)
    if status is not None:
        query = query.where(Page.status == status)
    if changed_after is not None:
        query = query.where(DocumentVersion.crawled_at >= changed_after)
    if changed_before is not None:
        query = query.where(DocumentVersion.crawled_at < changed_before)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(DocumentVersion.crawled_at.desc(), Page.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [DocumentRow(page, version) for page, version in rows], total


def get_document(session: Session, page_id: int) -> DocumentRow | None:
    row = session.execute(_current().where(Page.id == page_id)).one_or_none()
    return DocumentRow(row[0], row[1]) if row else None


def version_links(session: Session, version_id: int) -> list[Link]:
    return list(
        session.scalars(select(Link).where(Link.version_id == version_id).order_by(Link.position))
    )


def version_count(session: Session, page_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.page_id == page_id)
        )
        or 0
    )


def get_page(session: Session, page_id: int) -> Page | None:
    return session.get(Page, page_id)


def raw_fetch(session: Session, page_id: int, fetch_id: int | None = None) -> Fetch | None:
    """One fetch of a page: the given one, or the latest that has HTML (a 304 has none)."""
    query = select(Fetch).where(Fetch.page_id == page_id)
    if fetch_id is not None:
        return session.scalars(query.where(Fetch.id == fetch_id)).one_or_none()
    return session.scalars(
        query.where(Fetch.html.is_not(None)).order_by(Fetch.fetched_at.desc(), Fetch.id.desc())
    ).first()


@dataclass(frozen=True)
class FetchSummary:
    id: int
    crawl_id: uuid.UUID
    fetched_at: datetime
    status_code: int | None
    outcome: str


def fetch_history(session: Session, page_id: int, limit: int = 20) -> list[FetchSummary]:
    """The page's latest fetches, without loading their HTML."""
    rows = session.execute(
        select(Fetch.id, Fetch.crawl_id, Fetch.fetched_at, Fetch.status_code, Fetch.outcome)
        .where(Fetch.page_id == page_id)
        .order_by(Fetch.fetched_at.desc(), Fetch.id.desc())
        .limit(limit)
    )
    return [FetchSummary(*row) for row in rows]


def list_versions(session: Session, page_id: int) -> list[DocumentVersion]:
    """Every version of a page, newest first (blocks are loaded only when asked for)."""
    return list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.page_id == page_id)
            .order_by(DocumentVersion.version.desc())
        )
    )


def get_version(session: Session, page_id: int, version: int) -> DocumentVersion | None:
    return session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.page_id == page_id, DocumentVersion.version == version
        )
    ).one_or_none()
