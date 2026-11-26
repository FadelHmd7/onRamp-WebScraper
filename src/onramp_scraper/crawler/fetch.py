"""Fetching pages over plain HTTP, politely.

``HttpFetcher`` sends one request and turns whatever happens into a
``FetchResult`` with an ``Outcome``. It never raises for network or HTTP
errors: the caller (the crawl task) decides what each outcome means.

``PoliteFetcher`` wraps it with the rules every request must follow:
robots.txt first, then a slot from the shared per-domain rate limiter, and,
when a site says "slow down" (429 or 503 with Retry-After), a pause for the
whole host so every worker waits, not just this one.

Redirects are not followed automatically. Following them inside the HTTP
client would let a redirect land on a page that robots.txt forbids, or on
another site, without either check running. Instead the target comes back
as ``Outcome.REDIRECT`` and goes through the normal pipeline as a new URL.
"""

import email.utils
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import httpx

from onramp_scraper.config import get_settings
from onramp_scraper.crawler.ratelimit import RateLimiter, effective_rate
from onramp_scraper.crawler.robots import RobotsChecker, RobotsFile, http_fetch_robots
from onramp_scraper.crawler.urls import host_of, normalize_url
from onramp_scraper.sites import SiteConfig

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_RETRY_AFTER = 3600.0  # never trust a site to park us for more than an hour

# Spec: retry on 429, 503 and connection errors. Timeouts and the other
# "try again later" gateway codes are the same kind of temporary failure.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
HTML_TYPES = ("text/html", "application/xhtml+xml")


class Outcome(StrEnum):
    OK = "ok"
    NOT_MODIFIED = "not_modified"
    REDIRECT = "redirect"
    RETRY = "retry"
    PERMANENT = "permanent"
    BLOCKED = "blocked"  # robots.txt disallows it; no request was sent


@dataclass(frozen=True)
class FetchResult:
    url: str
    outcome: Outcome
    status: int | None = None  # None when no HTTP response arrived
    body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    elapsed_ms: int = 0
    error: str | None = None
    retry_after: float | None = None  # seconds, from the Retry-After header
    location: str | None = None  # absolute, normalized redirect target

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After is either seconds ("120") or an HTTP date. Returns seconds, capped."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(float(value), MAX_RETRY_AFTER)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = (when - (now or datetime.now(UTC))).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def classify_status(
    url: str, status: int, headers: dict[str, str], elapsed_ms: int
) -> FetchResult | None:
    """The outcome for every status except success; ``None`` means "2xx, read the page".

    Shared by the HTTP and browser fetchers, so both follow the same rules.
    ``headers`` must have lowercase names.
    """

    def result(
        outcome: Outcome,
        error: str | None = None,
        retry_after: float | None = None,
        location: str | None = None,
    ) -> FetchResult:
        return FetchResult(
            url=url,
            outcome=outcome,
            status=status,
            headers=headers,
            error=error,
            retry_after=retry_after,
            location=location,
            elapsed_ms=elapsed_ms,
        )

    if status == 304:
        return result(Outcome.NOT_MODIFIED)
    if 300 <= status < 400:
        raw_location = headers.get("location", "").strip()
        # An empty Location would resolve to this same page: a redirect loop.
        location = normalize_url(raw_location, base=url) if raw_location else None
        if location is None or location == normalize_url(url):
            return result(Outcome.PERMANENT, error=f"redirect {status} without a usable Location")
        return result(Outcome.REDIRECT, location=location)
    if status in RETRYABLE_STATUS:
        retry_after = parse_retry_after(headers.get("retry-after"))
        return result(Outcome.RETRY, error=f"HTTP {status}", retry_after=retry_after)
    if not 200 <= status < 300:
        return result(Outcome.PERMANENT, error=f"HTTP {status}")
    return None


class HttpFetcher:
    """One request, one classified result."""

    def __init__(self, user_agent: str, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def fetch(
        self, url: str, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        conditional = {}
        if etag:
            conditional["If-None-Match"] = etag
        if last_modified:
            conditional["If-Modified-Since"] = last_modified

        started = time.monotonic()
        try:
            with self.client.stream("GET", url, headers=conditional) as response:
                return self._classify(url, response, started)
        except httpx.TimeoutException as exc:
            return self._failed(url, Outcome.RETRY, f"timeout: {type(exc).__name__}", started)
        except httpx.TransportError as exc:  # connection refused, reset, DNS, protocol
            return self._failed(url, Outcome.RETRY, f"connection error: {exc}", started)
        except httpx.InvalidURL as exc:
            return self._failed(url, Outcome.PERMANENT, f"invalid url: {exc}", started)

    def _classify(self, url: str, response: httpx.Response, started: float) -> FetchResult:
        status = response.status_code
        headers = {name.lower(): value for name, value in response.headers.items()}
        early = classify_status(url, status, headers, _ms(started))
        if early is not None:
            return early

        def failed(error: str) -> FetchResult:
            return FetchResult(
                url=url,
                outcome=Outcome.PERMANENT,
                status=status,
                headers=headers,
                error=error,
                elapsed_ms=_ms(started),
            )

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in HTML_TYPES:
            return failed(f"not HTML: {content_type}")

        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return failed(f"page larger than {MAX_BODY_BYTES} bytes")
            chunks.append(chunk)
        body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        return FetchResult(
            url=url,
            outcome=Outcome.OK,
            status=status,
            headers=headers,
            body=body,
            elapsed_ms=_ms(started),
        )

    @staticmethod
    def _failed(url: str, outcome: Outcome, error: str, started: float) -> FetchResult:
        return FetchResult(url=url, outcome=outcome, error=error, elapsed_ms=_ms(started))


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


class BrowserFetch(Protocol):
    """What PoliteFetcher needs from a browser fetcher (see crawler/browser.py)."""

    def fetch(
        self, url: str, wait_until: str, wait_for: str | None, settle_ms: int = 0
    ) -> FetchResult: ...


def _default_browser() -> BrowserFetch:
    # Imported here so images without Playwright (the API) never import it.
    from onramp_scraper.crawler.browser import BrowserFetcher

    return BrowserFetcher(get_settings().user_agent)


class PoliteFetcher:
    """robots.txt check, then a shared rate-limit slot, then HTTP or a browser.

    The site's ``fetch_mode`` decides which. The browser is created on the
    first page that needs it, so a worker crawling only static sites never
    starts Chromium.
    """

    def __init__(
        self,
        http: HttpFetcher,
        robots: RobotsChecker,
        limiter: RateLimiter,
        browser_factory: Callable[[], BrowserFetch] = _default_browser,
    ) -> None:
        self.http = http
        self.robots = robots
        self.limiter = limiter
        self._browser_factory = browser_factory
        self._browser: BrowserFetch | None = None

    @property
    def browser(self) -> BrowserFetch:
        if self._browser is None:
            self._browser = self._browser_factory()
        return self._browser

    def fetch(
        self,
        url: str,
        site: SiteConfig,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        if not self.robots.allowed(url):
            return FetchResult(url=url, outcome=Outcome.BLOCKED, error="disallowed by robots.txt")

        host = host_of(url)
        rate = effective_rate(site.requests_per_second, self.robots.crawl_delay(url))
        self.limiter.wait(host, rate)
        if site.fetch_mode == "browser":
            # One page load counts as one request against the limit.
            result = self.browser.fetch(
                url, site.browser_wait_until, site.browser_wait_for, site.browser_settle_ms
            )
        else:
            result = self.http.fetch(url, etag=etag, last_modified=last_modified)

        if result.status in (429, 503) and result.retry_after:
            self.limiter.pause(host, result.retry_after)
        return result


def rate_limited_robots_fetch(
    limiter: RateLimiter, requests_per_second: Callable[[str], float]
) -> Callable[[str, str], RobotsFile]:
    """A robots.txt fetch that also takes a rate-limit slot, so it counts toward the limit."""

    def fetch(url: str, user_agent: str) -> RobotsFile:
        limiter.wait(host_of(url), requests_per_second(url))
        return http_fetch_robots(url, user_agent)

    return fetch
