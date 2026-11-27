"""The dead-letter queue: URLs that failed every attempt, kept for an admin to retry.

A row's status moves ``dead`` -> ``retried`` (an admin sent it again) ->
``resolved`` (the retry succeeded), or back to ``dead`` if it failed again.
One row per ``(crawl_id, url)``, so a URL that dies twice updates its row.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp_scraper.storage import crawls
from onramp_scraper.storage.models import DeadLetter


class CrawlCancelled(Exception):
    """The dead letter's crawl was cancelled, so its job would be dropped unrun."""

    def __init__(self, crawl_id: uuid.UUID) -> None:
        super().__init__(f"its crawl {crawl_id} was cancelled, so the job cannot run again")
        self.crawl_id = crawl_id


def bury(
    session: Session,
    *,
    crawl_id: uuid.UUID,
    site_key: str,
    url: str,
    depth: int,
    attempts: int,
    error: str,
    status_code: int | None,
) -> None:
    values = {
        "attempts": attempts,
        "last_error": error,
        "last_status": status_code,
        "status": "dead",
        "failed_at": datetime.now(UTC),
    }
    session.execute(
        insert(DeadLetter)
        .values(crawl_id=crawl_id, site_key=site_key, url=url, depth=depth, **values)
        .on_conflict_do_update(constraint="uq_dead_letters_crawl_id_url", set_=values)
    )


def resolve(session: Session, crawl_id: uuid.UUID, url: str) -> None:
    """A retried URL succeeded: it leaves the queue."""
    session.execute(
        update(DeadLetter)
        .where(
            DeadLetter.crawl_id == crawl_id, DeadLetter.url == url, DeadLetter.status == "retried"
        )
        .values(status="resolved")
    )


def take_for_retry(session: Session, dead_letter_id: int) -> DeadLetter | None:
    """Mark a dead URL as retried and queue it in its crawl again. None if it is not dead.

    Raises ``CrawlCancelled`` if its crawl was cancelled: workers drop that
    crawl's jobs, so the row would otherwise say "retried" forever.

    The caller sends the job after this transaction commits.
    """
    row = session.scalars(
        select(DeadLetter).where(DeadLetter.id == dead_letter_id).with_for_update()
    ).one_or_none()
    if row is None or row.status != "dead":
        return None
    if crawls.is_cancelled(session, row.crawl_id):
        raise CrawlCancelled(row.crawl_id)
    row.status = "retried"
    row.retried_at = datetime.now(UTC)
    crawls.set_url_status(
        session, row.crawl_id, row.url, depth=row.depth, status="queued", attempts=0
    )
    crawls.reopen(session, row.crawl_id)
    return row


def list_dead(
    session: Session, status: str = "dead", limit: int = 50, offset: int = 0
) -> tuple[list[DeadLetter], int]:
    """A page of dead letters, newest first, and the total count."""
    query = select(DeadLetter).where(DeadLetter.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(DeadLetter.failed_at.desc(), DeadLetter.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def put_back(session: Session, dead_letter_id: int) -> None:
    """Undo ``take_for_retry`` when the job could not be sent."""
    row = session.get(DeadLetter, dead_letter_id, with_for_update=True)
    if row is not None:
        _return_to_queue(session, row)


def release(session: Session, crawl_id: uuid.UUID, url: str) -> bool:
    """A retried job that will not run (its crawl was cancelled) waits in the queue again.

    Without this the row would say "retried" forever. True if a row went back.
    """
    row = session.scalars(
        select(DeadLetter)
        .where(DeadLetter.crawl_id == crawl_id, DeadLetter.url == url)
        .with_for_update()
    ).one_or_none()
    return row is not None and _return_to_queue(session, row)


def _return_to_queue(session: Session, row: DeadLetter) -> bool:
    if row.status != "retried":
        return False
    row.status = "dead"
    row.retried_at = None
    crawls.set_url_status(
        session,
        row.crawl_id,
        row.url,
        depth=row.depth,
        status="dead",
        attempts=row.attempts,
        error=row.last_error,
    )
    return True
