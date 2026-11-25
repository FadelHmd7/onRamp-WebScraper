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
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.crawler.fetch import FetchResult, Outcome, PoliteFetcher
from onramp_scraper.crawler.frontier import Frontier, extract_links, in_scope, page_nofollow
from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.changes import (
    Decision,
    StoredPage,
    conditional_headers,
    unchanged_by_headers,
)
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.document import InvalidDocument
from onramp_scraper.processing.fields import extract_fields
from onramp_scraper.sites import SiteConfig
from onramp_scraper.storage import crawls, dead_letters
from onramp_scraper.storage.models import Crawl
from onramp_scraper.storage.versions import load_page, mark_removed, stored_links, sweep_unreached
from onramp_scraper.storage.writes import store_not_modified, store_page
from onramp_scraper.worker.retry import RetryPolicy

log = logging.getLogger(__name__)

GONE = (404, 410)  # the page no longer exists: marked removed, its versions kept


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
    retry: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass
class JobResult:
    """What one run did, for logs and tests."""

    status: str  # the URL's final status, or "retrying" / "duplicate" / "cancelled"
    reason: str = ""
    children: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Handled:
    """A fetch's outcome: the URL's status, why, and the URLs it leads to."""

    status: str
    reason: str
    candidates: list[str] = field(default_factory=list)  # in scope, in page order
    child_depth: int = 0
    retry_after: float | None = None  # seconds the server asked us to wait
    http_status: int | None = None


def process(job: Job, ctx: CrawlContext, redelivered: bool = False) -> JobResult:
    crawl_id = uuid.UUID(job.crawl_id)
    site = ctx.sites[job.site_key]
    with ctx.sessions() as session:
        mode = session.scalar(
            select(Crawl.mode).where(Crawl.id == crawl_id, Crawl.status != "failed")
        )
        if mode is None:
            return JobResult("cancelled")
        state = crawls.url_state(session, crawl_id, job.url)
        if state is not None and state.status in crawls.FINAL_STATUSES:
            # A duplicate message for a URL that is already done.
            return JobResult("duplicate", f"already {state.status}")
        # Incremental: send the stored validators back, so an unchanged page
        # costs a 304 and no download. A full crawl downloads everything.
        stored = load_page(session, job.url) if mode == "incremental" else None

    try:
        fetch = ctx.fetcher.fetch(job.url, site, **conditional_headers(stored))
        handled = _handle(job, site, fetch, ctx, stored)
    except Exception as exc:  # a bug or a time limit: counts as a failed attempt
        log.exception("job for %s raised", job.url)
        handled = Handled("retry", f"{type(exc).__name__}: {exc}")
    if handled.status == "retry":
        return _retry_or_bury(job, handled, ctx)

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
        dead_letters.resolve(session, crawl_id, job.url)  # if it was a dead-letter retry
    _finish_if_done(ctx, crawl_id)
    log.info("%s %s (%s) +%d links", handled.status, job.url, handled.reason, len(sent))
    return JobResult(handled.status, handled.reason, sent)


def _finish_if_done(ctx: CrawlContext, crawl_id: uuid.UUID) -> None:
    with ctx.sessions.begin() as session:  # a new transaction: it sees this job's commit
        if not crawls.finish_if_done(session, crawl_id):
            return
        log.info("crawl %s finished", crawl_id)
        crawl = session.get(Crawl, crawl_id)
        if crawl is None or crawl.mode != "full" or crawl.seeds is not None:
            return  # only a full crawl from the configured seeds sees the whole site
        site = ctx.sites[crawl.site_key]
        if ctx.frontier.accepted_count(str(crawl_id)) >= site.max_pages:
            log.info("crawl %s hit its page budget; no pages marked removed", crawl_id)
            return
        when = crawl.finished_at or datetime.now(UTC)
        removed = sweep_unreached(session, crawl_id, crawl.site_key, when)
        log.info("crawl %s: %d pages not reached, marked removed", crawl_id, removed)


def _retry_or_bury(job: Job, handled: Handled, ctx: CrawlContext) -> JobResult:
    """Send the job again after a backoff, or move it to the dead-letter queue.

    The retry is sent before the row is updated: a crash in between leaves
    the URL queued with a retry on its way, never queued with nothing coming.
    """
    crawl_id = uuid.UUID(job.crawl_id)
    if ctx.retry.should_retry(job.attempt):
        delay = ctx.retry.delay(job.attempt, handled.retry_after)
        ctx.enqueue([replace(job, attempt=job.attempt + 1)], delay)
        with ctx.sessions.begin() as session:
            crawls.set_url_status(
                session,
                crawl_id,
                job.url,
                depth=job.depth,
                status="queued",
                attempts=job.attempt,
                error=handled.reason,
            )
        log.warning(
            "attempt %d for %s failed (%s); retry in %.1fs",
            job.attempt,
            job.url,
            handled.reason,
            delay,
        )
        return JobResult("retrying", f"{handled.reason}; retry in {delay:.1f}s")

    with ctx.sessions.begin() as session:
        dead_letters.bury(
            session,
            crawl_id=crawl_id,
            site_key=job.site_key,
            url=job.url,
            depth=job.depth,
            attempts=job.attempt,
            error=handled.reason,
            status_code=handled.http_status,
        )
        crawls.set_url_status(
            session,
            crawl_id,
            job.url,
            depth=job.depth,
            status="dead",
            attempts=job.attempt,
            error=handled.reason,
        )
    _finish_if_done(ctx, crawl_id)
    log.error("%s is dead after %d attempts: %s", job.url, job.attempt, handled.reason)
    return JobResult("dead", handled.reason)


def _handle(
    job: Job, site: SiteConfig, fetch: FetchResult, ctx: CrawlContext, stored: StoredPage | None
) -> Handled:
    if (decision := unchanged_by_headers(stored, fetch)) is not None:
        return _not_modified(job, site, fetch, ctx, decision)
    if fetch.outcome is Outcome.OK:
        return _store(job, site, fetch, ctx)
    if fetch.outcome is Outcome.REDIRECT and fetch.location:
        # The target is crawled as its own URL, at the same depth as this one.
        target = fetch.location
        scoped = [target] if in_scope(site, target, job.depth) else []
        return Handled("skipped", f"redirect to {target}", scoped, job.depth)
    if fetch.outcome is Outcome.BLOCKED:
        return Handled("skipped", fetch.error or "disallowed by robots.txt")
    if fetch.outcome is Outcome.RETRY:  # timeout, connection error, 429, 5xx
        reason = fetch.error or "temporary failure"
        return Handled("retry", reason, retry_after=fetch.retry_after, http_status=fetch.status)
    reason = fetch.error or str(fetch.outcome)
    if fetch.status in GONE:
        with ctx.sessions.begin() as session:
            if mark_removed(session, job.url, fetch.fetched_at):
                reason += ": page marked removed"
    return Handled("failed", reason, http_status=fetch.status)


def _not_modified(
    job: Job, site: SiteConfig, fetch: FetchResult, ctx: CrawlContext, decision: Decision
) -> Handled:
    """304 or the same ETag: nothing to download or parse. Links come from the stored version."""
    with ctx.sessions.begin() as session:
        store_not_modified(
            session, site=site, crawl_id=uuid.UUID(job.crawl_id), fetch=fetch, decision=decision
        )
        links = stored_links(session, job.url)
    candidates = Frontier.candidates(site, links, job.depth)
    return Handled("unchanged", decision.reason, candidates, job.depth + 1)


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
