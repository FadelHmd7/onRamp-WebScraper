"""Crawl runs: start one, follow its progress, finish it.

``start_crawl`` creates the run, puts the seeds through the frontier, records
them as ``queued`` and sends their jobs. The API (task A2) and the command
line (``python -m onramp_scraper.worker.cli``) both call it.
"""

import uuid

from onramp_scraper.crawler.frontier import in_scope
from onramp_scraper.crawler.urls import normalize_url
from onramp_scraper.storage import crawls
from onramp_scraper.worker.pipeline import CrawlContext, Job

MODES = ("full", "incremental")


def start_crawl(
    ctx: CrawlContext,
    site_key: str,
    mode: str = "full",
    seeds: list[str] | None = None,
    created_by: int | None = None,
) -> uuid.UUID:
    """Start a crawl of one site. ``seeds`` replaces the configured seeds (demos, retries).

    Seeds must be in the site's scope (allowed host and include patterns);
    robots.txt is still checked for each one when it is fetched.
    """
    if site_key not in ctx.sites:
        raise ValueError(f"unknown site {site_key!r}")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    site = ctx.sites[site_key]
    raw = seeds if seeds is not None else [str(s) for s in site.seeds]
    urls = [u for s in raw if (u := normalize_url(s)) is not None]
    outside = [u for u in urls if not in_scope(site, u, 0)]
    if outside or not urls:
        raise ValueError(f"seeds outside site {site_key!r}: {outside or raw}")

    with ctx.sessions.begin() as session:
        crawl_id = crawls.create_crawl(session, site_key, mode, created_by).id
    accepted = ctx.frontier.offer(str(crawl_id), site, urls)
    with ctx.sessions.begin() as session:
        crawls.record_queued(session, crawl_id, accepted, 0)
    ctx.enqueue([Job(str(crawl_id), site_key, url, 0) for url in accepted], 0.0)
    return crawl_id
