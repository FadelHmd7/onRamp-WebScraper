"""Keyword search with highlighted snippets, using PostgreSQL's full-text index."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import CurrentUser, SessionDep
from onramp_scraper.api.schemas import Page
from onramp_scraper.storage import search as store

router = APIRouter(tags=["search"])


class SearchHit(BaseModel):
    id: int = Field(description="Document id: open it with GET /documents/{id}")
    url: str
    site: str
    title: str
    version: int
    crawled_at: datetime
    rank: float = Field(description="ts_rank relevance; title matches weigh more")
    snippet: str = Field(
        description="Safe HTML: the page text is escaped and matches are wrapped in <mark>"
    )


class SearchResults(Page[SearchHit]):
    query: str


@router.get("/search", response_model=SearchResults)
def search(
    _: CurrentUser,
    session: SessionDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=200,
            description='Words to find. Also: "exact phrase", -exclude, this or that.',
        ),
    ],
    site: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResults:
    """Search the current version of every active document, most relevant first."""
    hits, total = store.search(session, q, site, limit, offset)
    items = [
        SearchHit(
            id=h.page_id,
            url=h.url,
            site=h.site_key,
            title=h.title,
            version=h.version,
            crawled_at=h.crawled_at,
            rank=h.rank,
            snippet=h.snippet,
        )
        for h in hits
    ]
    return SearchResults(query=q, items=items, total=total, limit=limit, offset=offset)
