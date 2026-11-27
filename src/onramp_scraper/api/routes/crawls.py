"""Crawls: start one (admin), list them and follow their progress (viewer)."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import AdminUser, CurrentUser, SessionDep, crawl_context
from onramp_scraper.api.schemas import Page
from onramp_scraper.storage import crawls as store
from onramp_scraper.storage.models import Crawl
from onramp_scraper.worker.pipeline import CrawlContext
from onramp_scraper.worker.runs import CrawlAlreadyRunning, start_crawl

router = APIRouter(prefix="/crawls", tags=["crawls"])


class CrawlCreate(BaseModel):
    site: str = Field(examples=["testsite"], description="A site key from config/sites.yaml")
    mode: Literal["full", "incremental"] = Field(
        default="full",
        description="incremental: send the stored ETag / Last-Modified, so unchanged pages "
        "cost a 304 and no download",
    )
    seeds: list[str] | None = Field(
        default=None,
        description="Crawl these URLs instead of the configured seeds (they must be in "
        "the site's scope). A crawl with custom seeds never marks pages removed.",
    )
    max_depth: int | None = Field(
        default=None, ge=0, description="Follow links less deep than the site allows"
    )


class CrawlError(BaseModel):
    url: str
    status: str  # failed, dead, or queued (waiting for a retry)
    attempts: int
    last_error: str | None
    updated_at: datetime


class CrawlOut(BaseModel):
    id: uuid.UUID
    site: str
    mode: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    seconds: float | None = Field(description="Running time so far, or in total")
    pages_per_minute: float | None
    total: int = Field(description="URLs accepted by the frontier so far")
    done: int
    counts: dict[str, int] = Field(
        description="URLs by status: queued, fetched, unchanged, skipped, failed, dead"
    )
    errors: int
    whole_site: bool = Field(
        description="True for a crawl of the site's configured seeds; false for custom seeds"
    )


class CrawlDetail(CrawlOut):
    recent_errors: list[CrawlError]


def _out(progress: store.CrawlProgress) -> CrawlOut:
    seconds = None
    if progress.started_at is not None:
        end = progress.finished_at or datetime.now(UTC)
        seconds = round((end - progress.started_at).total_seconds(), 1)
    rate = round(progress.done / seconds * 60, 1) if seconds else None
    return CrawlOut(
        id=progress.crawl_id,
        site=progress.site_key,
        mode=progress.mode,
        status=progress.status,
        created_at=progress.created_at,
        started_at=progress.started_at,
        finished_at=progress.finished_at,
        seconds=seconds,
        pages_per_minute=rate,
        total=progress.total,
        done=progress.done,
        counts=progress.counts,
        errors=progress.errors,
        whole_site=progress.whole_site,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CrawlOut)
def create_crawl(
    body: CrawlCreate,
    user: AdminUser,
    session: SessionDep,
    ctx: Annotated[CrawlContext, Depends(crawl_context)],
    response: Response,
) -> CrawlOut:
    """Start a crawl (admin). Returns at once; the workers do the crawling.

    Follow it with `GET /crawls/{id}`. 409 if the site already has a whole-site
    crawl queued or running (crawls with custom `seeds` are always accepted).
    """
    try:
        crawl_id = start_crawl(
            ctx, body.site, body.mode, body.seeds, user.id, body.max_depth, exclusive=True
        )
    except CrawlAlreadyRunning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    response.headers["Location"] = f"/crawls/{crawl_id}"
    found = store.progress(session, crawl_id)
    if found is None:  # cannot happen: start_crawl committed it
        raise HTTPException(status_code=500, detail="Crawl was not recorded")
    return _out(found)


@router.get("", response_model=Page[CrawlOut])
def list_crawls(
    _: CurrentUser,
    session: SessionDep,
    site: str | None = None,
    crawl_status: Annotated[
        Literal["queued", "running", "finished", "failed"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CrawlOut]:
    """Crawls, newest first, with their progress."""
    rows, total = store.list_crawls(session, site, crawl_status, limit, offset)
    return Page(items=[_out(p) for p in rows], total=total, limit=limit, offset=offset)


@router.get("/{crawl_id}", response_model=CrawlDetail)
def get_crawl(crawl_id: uuid.UUID, _: CurrentUser, session: SessionDep) -> CrawlDetail:
    """One crawl's progress, and its most recent errors (failed, dead, or retrying)."""
    found = store.progress(session, crawl_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such crawl")
    errors = [
        CrawlError(
            url=row.url,
            status=row.status,
            attempts=row.attempts,
            last_error=row.last_error,
            updated_at=row.updated_at,
        )
        for row in store.recent_errors(session, crawl_id)
    ]
    return CrawlDetail(**_out(found).model_dump(), recent_errors=errors)


UrlStatus = Literal["queued", "fetched", "unchanged", "skipped", "failed", "dead"]


class CrawlUrlOut(BaseModel):
    url: str
    status: UrlStatus
    depth: int
    attempts: int
    last_error: str | None
    updated_at: datetime


@router.get("/{crawl_id}/urls", response_model=Page[CrawlUrlOut])
def list_crawl_urls(
    crawl_id: uuid.UUID,
    _: CurrentUser,
    session: SessionDep,
    url_status: Annotated[list[UrlStatus] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CrawlUrlOut]:
    """The crawl's URLs, most recently updated first. Repeat `status` to filter by several."""
    if session.get(Crawl, crawl_id) is None:
        raise HTTPException(status_code=404, detail="No such crawl")
    rows, total = store.list_urls(session, crawl_id, list(url_status or []), limit, offset)
    items = [
        CrawlUrlOut(
            url=row.url,
            status=row.status,  # type: ignore[arg-type]  # checked by a database constraint
            depth=row.depth,
            attempts=row.attempts,
            last_error=row.last_error,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)
