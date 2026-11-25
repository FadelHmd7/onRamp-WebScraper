"""One crawl job: fetch -> clean -> validate -> store -> enqueue links.

This module knows nothing about Celery. ``worker/app.py`` wraps ``process``
in a task; tests drive it with plain threads and an in-memory queue.

Delivery is at least once (``acks_late``), so a job can run twice. Each step
is safe to repeat, and the order of the last steps is what keeps a crash
from losing pages:

1. store the page (one transaction; idempotent, task D7)
2. send the child jobs for newly accepted links
3. record the children's rows, then mark this URL final (one transaction)

A crash before step 3 leaves this URL ``queued``, so the crawl cannot finish
and RabbitMQ redelivers the job. The redelivered run finds the children the
first run accepted in Redis but never recorded, and sends them again. A
crash after step 3 loses nothing: the job is already complete.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.crawler.fetch import FetchResult, Outcome, PoliteFetcher
from onramp_scraper.crawler.frontier import Frontier, extract_links, in_scope, page_nofollow
from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.document import InvalidDocument
from onramp_scraper.processing.fields import extract_fields
from onramp_scraper.sites import SiteConfig
from onramp_scraper.storage import crawls
from onramp_scraper.storage.models import Crawl
from onramp_scraper.storage.writes import store_page

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    """One URL to crawl. Sent as JSON, so every field is a plain value."""

    crawl_id: str
    site_key: str
    url: str
    depth: int
    attempt: int = 1

    def to_message(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "Job":
        return cls(**message)

    def child(self, url: str, depth: int) -> "Job":
        return Job(self.crawl_id, self.site_key, url, depth)


# Sends jobs to the queue, optionally after a delay in seconds.
Enqueue = Callable[[list[Job], float], None]


@dataclass
class CrawlContext:
    """What a worker needs. Built once per worker process (``worker/runtime.py``)."""

    sessions: sessionmaker[Session]
    fetcher: PoliteFetcher
    frontier: Frontier
    sites: dict[str, SiteConfig]
    enqueue: Enqueue


@dataclass
class JobResult:
    """What one run did, for logs and tests."""

    status: str  # the URL's final status, or "duplicate" / "cancelled"
    reason: str = ""
    children: list[str] = field(default_factory=list)


def _cancelled(session: Session, crawl_id: uuid.UUID) -> bool:
    status = session.scalar(select(Crawl.status).where(Crawl.id == crawl_id))
    return status is None or status == "failed"


def process(job: Job, ctx: CrawlContext, redelivered: bool = False) -> JobResult:
    crawl_id = uuid.UUID(job.crawl_id)
    site = ctx.sites[job.site_key]
    with ctx.sessions() as session:
        if _cancelled(session, crawl_id):
            return JobResult("cancelled")
        state = crawls.url_state(session, crawl_id, job.url)
        if state is not None and state.status in crawls.FINAL_STATUSES:
            # A duplicate message for a URL that is already done.
            return JobResult("duplicate", f"already {state.status}")

    fetch = ctx.fetcher.fetch(job.url, site)
    handled = _handle(job, site, fetch, ctx)
    sent = _send_children(job, handled, ctx, redelivered)
    with ctx.sessions.begin() as session:
        crawls.record_queued(session, crawl_id, sent, handled.child_depth)
        crawls.set_url_status(
            session,
            crawl_id,
            job.url,
            depth=job.depth,
            status=handled.status,
            attempts=job.attempt,
            error=handled.reason if handled.status in ("failed", "skipped") else None,
        )
    log.info("%s %s (%s) +%d links", handled.status, job.url, handled.reason, len(sent))
    return JobResult(handled.status, handled.reason, sent)


@dataclass(frozen=True)
class Handled:
    """A fetch's outcome: the URL's status, why, and the URLs it leads to."""

    status: str
    reason: str
    candidates: list[str] = field(default_factory=list)  # in scope, in page order
    child_depth: int = 0


def _handle(job: Job, site: SiteConfig, fetch: FetchResult, ctx: CrawlContext) -> Handled:
    if fetch.outcome is Outcome.OK:
        return _store(job, site, fetch, ctx)
    if fetch.outcome is Outcome.REDIRECT and fetch.location:
        # The target is crawled as its own URL, at the same depth as this one.
        target = fetch.location
        scoped = [target] if in_scope(site, target, job.depth) else []
        return Handled("skipped", f"redirect to {target}", scoped, job.depth)
    if fetch.outcome is Outcome.BLOCKED:
        return Handled("skipped", fetch.error or "disallowed by robots.txt")
    return Handled("failed", fetch.error or str(fetch.outcome))


def _store(job: Job, site: SiteConfig, fetch: FetchResult, ctx: CrawlContext) -> Handled:
    html = fetch.body or ""
    soup = parse_html(html)  # links and fields read the page as fetched
    links = extract_links(soup, job.url)  # stored as found, nofollow ones included
    follow = not page_nofollow(soup)
    candidates = Frontier.candidates(site, links, job.depth, follow)
    fields = extract_fields(soup, site.fields)
    cleaned = clean_html(html, site.title_selector, site.remove)
    try:
        with ctx.sessions.begin() as session:
            saved = store_page(
                session,
                site=site,
                crawl_id=uuid.UUID(job.crawl_id),
                fetch=fetch,
                cleaned=cleaned,
                fields=fields,
                links=links,
            )
    except InvalidDocument as exc:
        # Not stored, but its links are still real links.
        return Handled("skipped", f"invalid document: {exc.reason}", candidates, job.depth + 1)
    status = "fetched" if saved.decision.is_new_version else "unchanged"
    return Handled(status, saved.decision.reason, candidates, job.depth + 1)


def _send_children(job: Job, handled: Handled, ctx: CrawlContext, redelivered: bool) -> list[str]:
    """Accept new URLs in the frontier and send their jobs. Returns the URLs sent."""
    site = ctx.sites[job.site_key]
    accepted = ctx.frontier.offer(job.crawl_id, site, handled.candidates)
    if redelivered:
        accepted += _orphans(job, handled.candidates, accepted, ctx)
    if accepted:
        ctx.enqueue([job.child(url, handled.child_depth) for url in accepted], 0.0)
    return accepted


def _orphans(job: Job, candidates: list[str], accepted: list[str], ctx: CrawlContext) -> list[str]:
    """URLs an earlier run of this job accepted but died before recording."""
    rest = [url for url in candidates if url not in accepted]
    seen = ctx.frontier.seen_many(job.crawl_id, rest)
    with ctx.sessions() as session:
        return crawls.unrecorded(session, uuid.UUID(job.crawl_id), seen)
