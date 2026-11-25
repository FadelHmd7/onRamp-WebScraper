"""Crawl runs: start one, follow its progress, finish it.

``start_crawl`` creates the run, puts the seeds through the frontier, records
them as ``queued`` and sends their jobs. The API (task A2) and the command
line (``python -m onramp_scraper.worker.cli``) both call it.
"""

import uuid

from onramp_scraper.crawler.frontier import in_scope
from onramp_scraper.crawler.urls import normalize_url
from onramp_scraper.storage import crawls, dead_letters
from onramp_scraper.worker.pipeline import CrawlContext, Job

MODES = ("full", "incremental")


def start_crawl(
    ctx: CrawlContext,
    site_key: str,
    mode: str = "full",
    seeds: list[str] | None = None,
    created_by: int | None = None,
    max_depth: int | None = None,
) -> uuid.UUID:
    """Start a crawl of one site.

    ``seeds`` replaces the configured seeds and ``max_depth`` lowers the site's
    depth limit (demos, re-checks: ``max_depth=0`` crawls only the seeds).
    Seeds must be in the site's scope (allowed host and include patterns);
    robots.txt is still checked for each one when it is fetched.
    """
    if site_key not in ctx.sites:
        raise ValueError(f"unknown site {site_key!r}")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    site = ctx.sites[site_key]
    if max_depth is not None and not 0 <= max_depth <= site.max_depth:
        raise ValueError(f"max_depth must be between 0 and the site's {site.max_depth}")
    raw = seeds if seeds is not None else [str(s) for s in site.seeds]
    urls = [u for s in raw if (u := normalize_url(s)) is not None]
    outside = [u for u in urls if not in_scope(site, u, 0)]
    if outside or not urls:
        raise ValueError(f"seeds outside site {site_key!r}: {outside or raw}")

    with ctx.sessions.begin() as session:
        custom = urls if seeds is not None else None
        crawl_id = crawls.create_crawl(session, site_key, mode, created_by, custom, max_depth).id
    accepted = ctx.frontier.offer(str(crawl_id), site, urls)
    with ctx.sessions.begin() as session:
        crawls.record_queued(session, crawl_id, accepted, 0)
    ctx.enqueue([Job(str(crawl_id), site_key, url, 0) for url in accepted], 0.0)
    return crawl_id


def retry_dead_letter(ctx: CrawlContext, dead_letter_id: int) -> bool:
    """Send a dead URL again (admin action). False if it is not in the dead-letter queue.

    The row is committed as "retried" before the job is sent; otherwise a fast
    worker could pick the job up, still see "dead", and drop it. If sending
    fails, the row goes back to "dead" so the admin can try again.
    """
    with ctx.sessions.begin() as session:
        row = dead_letters.take_for_retry(session, dead_letter_id)
        if row is None:
            return False
        job = Job(str(row.crawl_id), row.site_key, row.url, row.depth)
    try:
        ctx.enqueue([job], 0.0)
    except Exception:
        with ctx.sessions.begin() as session:
            dead_letters.put_back(session, dead_letter_id)
        raise
    return True
