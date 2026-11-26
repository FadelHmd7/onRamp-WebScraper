"""Change detection: does a fetched page need a new version? (Required test area 3.)

Checked in order, cheapest first:

1. **304 Not Modified.** We sent the stored ``ETag`` / ``Last-Modified`` back
   (``If-None-Match`` / ``If-Modified-Since``) and the server says nothing
   changed. No body was downloaded; nothing is parsed or hashed.
2. **Same ETag.** Some servers ignore conditional requests but still send an
   ETag. If it equals the stored one, the content is the same; the page is
   not parsed or hashed either.
3. **Same content hash.** Everything else is cleaned and hashed
   (``processing/document.content_hash``: title + blocks + fields). The same
   hash means the same content, even when the HTML around it changed (ads,
   timestamps, session tokens): **no new version**.
4. **Different hash:** a new version, ``current + 1``. Old versions are kept.

A page seen for the first time gets version 1. A ``Last-Modified`` date
alone is never trusted as "unchanged": it has one-second resolution and
servers often set it to the time of the request.

These functions only decide. ``storage/versions.py`` applies the decision
inside a transaction that locks the page row, so two workers can never both
create "version 2".
"""

from dataclasses import dataclass
from enum import StrEnum

from onramp_scraper.crawler.fetch import FetchResult, Outcome


class Change(StrEnum):
    NEW = "new"  # first version of a page
    CHANGED = "changed"  # different content: version + 1
    UNCHANGED = "unchanged"  # no new version


@dataclass(frozen=True)
class StoredPage:
    """What we know about a page from its last successful fetch."""

    current_version: int | None = None
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    @property
    def has_version(self) -> bool:
        return self.current_version is not None


@dataclass(frozen=True)
class Decision:
    change: Change
    version: int  # the version to store, or the current one when unchanged
    reason: str  # for logs and the crawl record

    @property
    def is_new_version(self) -> bool:
        return self.change is not Change.UNCHANGED


def unchanged_by_headers(stored: StoredPage | None, fetch: FetchResult) -> Decision | None:
    """Steps 1 and 2: a decision from the response alone, or None to parse and hash."""
    if stored is None or stored.current_version is None:
        return None  # nothing stored yet to compare with
    if fetch.outcome is Outcome.NOT_MODIFIED:
        return Decision(Change.UNCHANGED, stored.current_version, "not modified (304)")
    if fetch.outcome is Outcome.OK and fetch.etag and fetch.etag == stored.etag:
        return Decision(Change.UNCHANGED, stored.current_version, "same ETag")
    return None


def decide(stored: StoredPage | None, content_hash: str) -> Decision:
    """Steps 3 and 4: compare the new content hash with the current version's."""
    if stored is None or stored.current_version is None:
        return Decision(Change.NEW, 1, "first version")
    if content_hash == stored.content_hash:
        return Decision(Change.UNCHANGED, stored.current_version, "same content hash")
    return Decision(Change.CHANGED, stored.current_version + 1, "content hash changed")


def conditional_headers(stored: StoredPage | None) -> dict[str, str]:
    """What to send back to the server; the fetcher turns these into If-* headers."""
    if stored is None or stored.current_version is None:
        return {}  # without a stored version, a 304 would leave us nothing to keep
    headers = {}
    if stored.etag:
        headers["etag"] = stored.etag
    if stored.last_modified:
        headers["last_modified"] = stored.last_modified
    return headers
