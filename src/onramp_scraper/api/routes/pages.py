"""Raw data: the HTML exactly as fetched, with the fetch's details."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import CurrentUser, SessionDep
from onramp_scraper.storage import documents as store

router = APIRouter(prefix="/pages", tags=["pages"])


class FetchSummary(BaseModel):
    id: int
    crawl_id: uuid.UUID
    fetched_at: datetime
    status_code: int | None
    outcome: str = Field(description="ok, not_modified (304), redirect, retry, permanent, ...")


class RawPage(BaseModel):
    page_id: int
    url: str
    site: str
    status: str
    fetch: FetchSummary
    elapsed_ms: int
    headers: dict[str, str] = Field(description="Response headers, lower-case names")
    error: str | None
    html: str | None = Field(
        description="The body as fetched. Returned as a JSON string, never as a page: "
        "serving another site's HTML from this API's origin would run its scripts here."
    )
    history: list[FetchSummary] = Field(description="The page's latest fetches, newest first")


@router.get("/{page_id}/raw", response_model=RawPage)
def raw_page(
    page_id: int,
    _: CurrentUser,
    session: SessionDep,
    fetch_id: Annotated[
        int | None,
        Query(description="A fetch from `history`. Default: the latest one with HTML."),
    ] = None,
) -> RawPage:
    """The raw HTML of a page and how it was fetched (status, headers, time, crawl)."""
    page = store.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="No such page")
    fetch = store.raw_fetch(session, page_id, fetch_id)
    if fetch is None:
        raise HTTPException(status_code=404, detail="No such fetch of this page")
    history = [FetchSummary(**vars(f)) for f in store.fetch_history(session, page_id)]
    return RawPage(
        page_id=page.id,
        url=page.url,
        site=page.site_key,
        status=page.status,
        fetch=FetchSummary(
            id=fetch.id,
            crawl_id=fetch.crawl_id,
            fetched_at=fetch.fetched_at,
            status_code=fetch.status_code,
            outcome=fetch.outcome,
        ),
        elapsed_ms=fetch.elapsed_ms,
        headers=fetch.headers,
        error=fetch.error,
        html=fetch.html,
        history=history,
    )
