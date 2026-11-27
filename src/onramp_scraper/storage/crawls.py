"""Crawl runs and the state of each URL inside one.

``crawl_urls`` is the crawl's ledger: one row per URL the frontier accepted,
``queued`` until its job ends with a final status. Every write here is keyed
on ``(crawl_id, url)``, so running a job twice leaves the same rows.

Statuses: ``queued`` (waiting or retrying), then one of ``fetched`` (a new
version was stored), ``unchanged`` (no new version), ``skipped`` (robots.txt,
redirect, invalid document), ``failed`` (permanent error) or ``dead`` (out of
retries; also in the dead-letter queue).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp_scraper.storage.models import Crawl, CrawlUrl

FINAL_STATUSES = frozenset({"fetched", "unchanged", "skipped", "failed", "dead"})


def create_crawl(
    session: Session,
    site_key: str,
    mode: str,
    created_by: int | None = None,
    seeds: list[str] | None = None,
    max_depth: int | None = None,
) -> Crawl:
    """A running crawl. ``seeds`` and ``max_depth`` only when they replace the site's."""
    crawl = Crawl(
        site_key=site_key,
        mode=mode,
        seeds=seeds,
        max_depth=max_depth,
        status="running",
        created_by=created_by,
        started_at=datetime.now(UTC),
    )
    session.add(crawl)
    session.flush()
    return crawl


def record_queued(session: Session, crawl_id: uuid.UUID, urls: list[str], depth: int) -> list[str]:
    """Rows for newly accepted URLs. Returns the URLs that had no row yet."""
    if not urls:
        return []
    rows = [{"crawl_id": crawl_id, "url": url, "depth": depth} for url in urls]
    inserted = session.scalars(
        insert(CrawlUrl).values(rows).on_conflict_do_nothing().returning(CrawlUrl.url)
    )
    return list(inserted)


def unrecorded(session: Session, crawl_id: uuid.UUID, urls: list[str]) -> list[str]:
    """The URLs with no row in this crawl, in the given order."""
    if not urls:
        return []
    known = set(
        session.scalars(
            select(CrawlUrl.url).where(CrawlUrl.crawl_id == crawl_id, CrawlUrl.url.in_(urls))
        )
    )
    return [url for url in urls if url not in known]


def url_state(session: Session, crawl_id: uuid.UUID, url: str) -> CrawlUrl | None:
    return session.get(CrawlUrl, (crawl_id, url))


def set_url_status(
    session: Session,
    crawl_id: uuid.UUID,
    url: str,
    *,
    depth: int,
    status: str,
    attempts: int,
    error: str | None = None,
) -> None:
    """Upsert the URL's row with its latest status (a job may arrive before its row)."""
    values = {
        "status": status,
        "attempts": attempts,
        "last_error": error,
        "updated_at": datetime.now(UTC),
    }
    session.execute(
        insert(CrawlUrl)
        .values(crawl_id=crawl_id, url=url, depth=depth, **values)
        .on_conflict_do_update(index_elements=[CrawlUrl.crawl_id, CrawlUrl.url], set_=values)
    )


STATUSES = ("queued", "fetched", "unchanged", "skipped", "failed", "dead")


@dataclass(frozen=True)
class CrawlProgress:
    """A crawl's state and its URL counts by status, straight from the database."""

    crawl_id: uuid.UUID
    site_key: str
    mode: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    counts: dict[str, int]
    whole_site: bool = True  # False: custom seeds (a demo, a re-check)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def done(self) -> int:
        return self.total - self.counts["queued"]

    @property
    def errors(self) -> int:
        return self.counts["failed"] + self.counts["dead"]


def progress(session: Session, crawl_id: uuid.UUID) -> CrawlProgress | None:
    crawl = session.get(Crawl, crawl_id)
    if crawl is None:
        return None
    counts = dict.fromkeys(STATUSES, 0)
    rows = session.execute(
        select(CrawlUrl.status, func.count())
        .where(CrawlUrl.crawl_id == crawl_id)
        .group_by(CrawlUrl.status)
    )
    for status, count in rows:
        counts[status] = count
    return _progress_of(crawl, counts)


def finish_if_done(session: Session, crawl_id: uuid.UUID) -> bool:
    """Mark the crawl finished when no URL is queued. True only for the call that did it.

    Called after a job commits its final status. A job records its children
    before it marks itself final, so "nothing queued" means nothing is left.
    """
    queued = session.scalar(
        select(func.count())
        .select_from(CrawlUrl)
        .where(CrawlUrl.crawl_id == crawl_id, CrawlUrl.status == "queued")
    )
    if queued:
        return False
    result = session.execute(
        update(Crawl)
        .where(Crawl.id == crawl_id, Crawl.status == "running")
        .values(status="finished", finished_at=datetime.now(UTC))
    )
    return bool(getattr(result, "rowcount", 0))


CANCELLED = "failed"  # a crawl stopped by hand; its remaining jobs are dropped


def is_cancelled(session: Session, crawl_id: uuid.UUID) -> bool:
    return session.scalar(select(Crawl.status).where(Crawl.id == crawl_id)) == CANCELLED


def reopen(session: Session, crawl_id: uuid.UUID) -> None:
    """A finished crawl runs again when one of its URLs is queued again (dead-letter retry)."""
    session.execute(
        update(Crawl)
        .where(Crawl.id == crawl_id, Crawl.status == "finished")
        .values(status="running", finished_at=None)
    )


def list_crawls(
    session: Session,
    site_key: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[CrawlProgress], int]:
    """A page of crawls, newest first, with their counts (one grouped query), and the total."""
    query = select(Crawl)
    if site_key is not None:
        query = query.where(Crawl.site_key == site_key)
    if status is not None:
        query = query.where(Crawl.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        session.scalars(
            query.order_by(Crawl.created_at.desc(), Crawl.id).limit(limit).offset(offset)
        )
    )
    counts: dict[uuid.UUID, dict[str, int]] = {c.id: dict.fromkeys(STATUSES, 0) for c in rows}
    if rows:
        grouped = session.execute(
            select(CrawlUrl.crawl_id, CrawlUrl.status, func.count())
            .where(CrawlUrl.crawl_id.in_(list(counts)))
            .group_by(CrawlUrl.crawl_id, CrawlUrl.status)
        )
        for crawl_id, url_status, count in grouped:
            counts[crawl_id][url_status] = count
    return [_progress_of(c, counts[c.id]) for c in rows], total


def _progress_of(crawl: Crawl, counts: dict[str, int]) -> CrawlProgress:
    return CrawlProgress(
        crawl_id=crawl.id,
        site_key=crawl.site_key,
        mode=crawl.mode,
        status=crawl.status,
        created_at=crawl.created_at,
        started_at=crawl.started_at,
        finished_at=crawl.finished_at,
        counts=counts,
        whole_site=crawl.seeds is None,
    )


def recent_errors(session: Session, crawl_id: uuid.UUID, limit: int = 20) -> list[CrawlUrl]:
    """URLs that failed, died, or are waiting for a retry, most recent first."""
    return list(
        session.scalars(
            select(CrawlUrl)
            .where(
                CrawlUrl.crawl_id == crawl_id,
                CrawlUrl.last_error.is_not(None),
                CrawlUrl.status.in_(("failed", "dead", "queued")),
            )
            .order_by(CrawlUrl.updated_at.desc())
            .limit(limit)
        )
    )


ACTIVE_STATUSES = ("queued", "running")


def active_whole_site_crawl(session: Session, site_key: str) -> Crawl | None:
    """The site's queued or running crawl of its configured seeds, if any."""
    return session.scalars(
        select(Crawl)
        .where(
            Crawl.site_key == site_key,
            # The JSONB column stores Python None as JSON null, not SQL NULL.
            or_(Crawl.seeds.is_(None), func.jsonb_typeof(Crawl.seeds) == "null"),
            Crawl.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Crawl.created_at.desc())
        .limit(1)
    ).first()


def list_urls(
    session: Session,
    crawl_id: uuid.UUID,
    statuses: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CrawlUrl], int]:
    """A page of the crawl's URLs, most recently updated first, and the total."""
    query = select(CrawlUrl).where(CrawlUrl.crawl_id == crawl_id)
    if statuses:
        query = query.where(CrawlUrl.status.in_(statuses))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(CrawlUrl.updated_at.desc(), CrawlUrl.url).limit(limit).offset(offset)
    )
    return list(rows), total
