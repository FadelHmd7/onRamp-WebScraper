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

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp_scraper.storage.models import Crawl, CrawlUrl

FINAL_STATUSES = frozenset({"fetched", "unchanged", "skipped", "failed", "dead"})


def create_crawl(
    session: Session, site_key: str, mode: str, created_by: int | None = None
) -> Crawl:
    crawl = Crawl(
        site_key=site_key,
        mode=mode,
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
    return CrawlProgress(
        crawl_id=crawl.id,
        site_key=crawl.site_key,
        mode=crawl.mode,
        status=crawl.status,
        created_at=crawl.created_at,
        started_at=crawl.started_at,
        finished_at=crawl.finished_at,
        counts=counts,
    )


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


def reopen(session: Session, crawl_id: uuid.UUID) -> None:
    """A finished crawl runs again when one of its URLs is queued again (dead-letter retry)."""
    session.execute(
        update(Crawl)
        .where(Crawl.id == crawl_id, Crawl.status == "finished")
        .values(status="running", finished_at=None)
    )
