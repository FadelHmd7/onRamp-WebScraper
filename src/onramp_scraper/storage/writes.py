"""Everything one crawled page writes, safe to run twice.

A job can run more than once: a worker may store a page and die before it
acknowledges the job, and RabbitMQ then delivers it again (that is what
``acks_late`` is for). Running the same job again must leave the same rows.
Each write is keyed on a natural unique key:

| Row                 | Key                     | On a second run                     |
| ------------------- | ----------------------- | ----------------------------------- |
| ``pages``           | ``url``                 | locked and touched, never duplicated |
| ``fetches``         | ``(crawl_id, page_id)`` | updated in place, same id           |
| ``document_versions`` | ``(page_id, version)`` | same hash: no new row; the first    |
|                     |                         | run's result is reported again      |
| ``links``           | ``(version_id, position)`` | ``ON CONFLICT DO NOTHING``       |

``store_page`` and ``store_not_modified`` do a page's writes in the caller's
transaction, so they all happen or none do.
"""

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp_scraper.crawler.fetch import FetchResult, Outcome
from onramp_scraper.crawler.frontier import Link as FoundLink
from onramp_scraper.processing.changes import Decision
from onramp_scraper.processing.clean import CleanedPage
from onramp_scraper.sites import SiteConfig
from onramp_scraper.storage.models import Fetch, Link
from onramp_scraper.storage.versions import Saved, lock_page, save_not_modified, save_page


def save_fetch(session: Session, crawl_id: uuid.UUID, page_id: int, fetch: FetchResult) -> int:
    """The raw response for this page in this crawl. A second run overwrites, same id."""
    values = {
        "fetched_at": fetch.fetched_at,
        "status_code": fetch.status,
        "outcome": str(fetch.outcome),
        "html": fetch.body if fetch.outcome is Outcome.OK else None,
        "headers": fetch.headers,
        "elapsed_ms": fetch.elapsed_ms,
        "error": fetch.error,
    }
    statement = (
        insert(Fetch)
        .values(crawl_id=crawl_id, page_id=page_id, **values)
        .on_conflict_do_update(constraint="uq_fetches_crawl_id_page_id", set_=values)
        .returning(Fetch.id)
    )
    return int(session.execute(statement).scalar_one())


def save_links(session: Session, version_id: int, links: list[FoundLink]) -> int:
    """A version's links in page order. Returns how many rows were new."""
    if not links:
        return 0
    rows: list[dict[str, Any]] = [
        {
            "version_id": version_id,
            "position": position,
            "target_url": link.url,
            "text": link.text,
            "nofollow": link.nofollow,
        }
        for position, link in enumerate(links)
    ]
    result = session.execute(insert(Link).values(rows).on_conflict_do_nothing())
    return int(getattr(result, "rowcount", 0))


def store_page(
    session: Session,
    *,
    site: SiteConfig,
    crawl_id: uuid.UUID,
    fetch: FetchResult,
    cleaned: CleanedPage,
    fields: dict[str, Any],
    links: list[FoundLink],
) -> Saved:
    """A fetched page: raw fetch, version if the content changed, and that version's links.

    Links belong to a version, so an unchanged page keeps its current
    version's links (they come from the same content).
    """
    page = lock_page(session, site.key, fetch.url, fetch.fetched_at)
    fetch_id = save_fetch(session, crawl_id, page.id, fetch)
    saved = save_page(
        session,
        site=site,
        url=fetch.url,
        cleaned=cleaned,
        fields=fields,
        crawled_at=fetch.fetched_at,
        etag=fetch.etag,
        last_modified=fetch.last_modified,
        crawl_id=crawl_id,
        fetch_id=fetch_id,
    )
    if saved.version_id is not None:
        save_links(session, saved.version_id, links)
    return saved


def store_not_modified(
    session: Session,
    *,
    site: SiteConfig,
    crawl_id: uuid.UUID,
    fetch: FetchResult,
    decision: Decision,
) -> Saved:
    """A 304 or same-ETag answer: the fetch is recorded, the page only touched."""
    page = lock_page(session, site.key, fetch.url, fetch.fetched_at)
    save_fetch(session, crawl_id, page.id, fetch)
    return save_not_modified(
        session,
        site=site,
        url=fetch.url,
        decision=decision,
        seen_at=fetch.fetched_at,
        etag=fetch.etag,
        last_modified=fetch.last_modified,
    )
