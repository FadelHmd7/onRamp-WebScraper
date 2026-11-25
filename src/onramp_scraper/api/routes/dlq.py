"""The dead-letter queue: URLs that failed every attempt (admin only)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import AdminUser, SessionDep, crawl_context
from onramp_scraper.api.schemas import Page
from onramp_scraper.storage import dead_letters as store
from onramp_scraper.storage.models import DeadLetter as DeadLetterRow
from onramp_scraper.worker.pipeline import CrawlContext
from onramp_scraper.worker.runs import retry_dead_letter

router = APIRouter(prefix="/dlq", tags=["dead-letter queue"])

Status = Literal["dead", "retried", "resolved"]


class DeadLetter(BaseModel):
    id: int
    crawl_id: uuid.UUID
    site: str
    url: str
    depth: int
    attempts: int = Field(description="Attempts made before it was given up")
    last_error: str
    last_status: int | None = Field(description="The last HTTP status, if a response came")
    status: Status = Field(
        description="dead: waiting for an admin; retried: sent again; resolved: the retry worked"
    )
    failed_at: datetime
    retried_at: datetime | None


def _out(row: DeadLetterRow) -> DeadLetter:
    return DeadLetter(
        id=row.id,
        crawl_id=row.crawl_id,
        site=row.site_key,
        url=row.url,
        depth=row.depth,
        attempts=row.attempts,
        last_error=row.last_error,
        last_status=row.last_status,
        status=row.status,  # type: ignore[arg-type]  # checked by a database constraint
        failed_at=row.failed_at,
        retried_at=row.retried_at,
    )


@router.get("", response_model=Page[DeadLetter])
def list_dead_letters(
    _: AdminUser,
    session: SessionDep,
    dlq_status: Annotated[Status, Query(alias="status")] = "dead",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DeadLetter]:
    """Failed jobs, most recent first. By default only those waiting for a retry."""
    rows, total = store.list_dead(session, dlq_status, limit, offset)
    return Page(items=[_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post(
    "/{dead_letter_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=DeadLetter
)
def retry(
    dead_letter_id: int,
    _: AdminUser,
    session: SessionDep,
    ctx: Annotated[CrawlContext, Depends(crawl_context)],
) -> DeadLetter:
    """Send a failed job again. It runs with fresh attempts in its original crawl.

    On success its status becomes "resolved"; if it fails every attempt again,
    it returns to "dead". 409 if it is not waiting (already retried or resolved).
    """
    row = session.get(DeadLetterRow, dead_letter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such dead letter")
    if not retry_dead_letter(ctx, dead_letter_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Not in the queue: its status is {row.status!r}",
        )
    session.expire_all()  # the retry committed in its own transaction
    refreshed = session.get(DeadLetterRow, dead_letter_id)
    if refreshed is None:  # cannot happen: rows are never deleted
        raise HTTPException(status_code=404, detail="No such dead letter")
    return _out(refreshed)
