"""The configured sites (config/sites.yaml), for choosing what to crawl."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import CurrentUser
from onramp_scraper.sites import SiteConfig, load_sites

router = APIRouter(tags=["sites"])


class Site(BaseModel):
    key: str
    name: str
    base_url: str
    role: str = Field(description="static, javascript, paginated or test")
    fetch_mode: str = Field(description="http, or browser for JavaScript-rendered sites")
    requests_per_second: float = Field(description="Shared by all workers for this site")
    max_pages: int
    max_depth: int


def get_sites() -> dict[str, SiteConfig]:
    return load_sites()


@router.get("/sites", response_model=list[Site])
def list_sites(
    _: CurrentUser, sites: Annotated[dict[str, SiteConfig], Depends(get_sites)]
) -> list[Site]:
    """Every site a crawl can be started for, with its limits."""
    return [
        Site(
            key=s.key,
            name=s.name,
            base_url=str(s.base_url),
            role=s.role,
            fetch_mode=s.fetch_mode,
            requests_per_second=s.requests_per_second,
            max_pages=s.max_pages,
            max_depth=s.max_depth,
        )
        for s in sites.values()
    ]
