"""Fetching JavaScript-rendered pages with headless Chromium (Playwright).

Used only for sites configured with ``fetch_mode: browser``; every other site
is fetched over plain HTTP. Only the worker image installs Playwright.

- Chromium starts on the first fetch, not at import, and is reused after that.
- Each page gets a fresh browser context (no cookies or storage carried over)
  with our User-Agent.
- Images, media and fonts are blocked: rendering the text does not need them,
  and skipping them saves the site bandwidth.
- Status codes follow the same rules as the HTTP fetcher (``classify_status``).
- The browser follows redirects by itself. If the page ends up on a different
  URL, the result is ``REDIRECT`` with that URL and no content, so the target
  goes through the robots.txt check like any other URL.
- No conditional requests: one page load is many requests (HTML, scripts,
  data). Change detection for these pages uses the content hash.
"""

import time
from typing import Literal, cast

from playwright.sync_api import Browser, Error, Page, Playwright, Route, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from onramp_scraper.crawler.fetch import (
    MAX_BODY_BYTES,
    FetchResult,
    Outcome,
    classify_status,
)
from onramp_scraper.crawler.urls import normalize_url

PAGE_TIMEOUT_MS = 45_000
SELECTOR_TIMEOUT_MS = 20_000
SETTLE_POLL_MS = 250
SETTLE_MAX_MS = 15_000  # a page that never stops changing is taken as it is then
BLOCKED_RESOURCES = frozenset({"image", "media", "font"})

WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]


def _block_heavy_resources(route: Route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCES:
        route.abort()
    else:
        route.continue_()


class BrowserFetcher:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @property
    def started(self) -> bool:
        return self._browser is not None

    def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def fetch(
        self,
        url: str,
        wait_until: str = "load",
        wait_for: str | None = None,
        settle_ms: int = 0,
    ) -> FetchResult:
        """Load and render a page.

        ``wait_for`` is an element that shows the page's data has arrived.
        ``settle_ms`` then waits until the page stops changing for that long:
        an app that loads sections one after another (Inventaire's list of an
        author's works) can be "network idle" with the element present and
        still be filling in the rest.
        """
        started = time.monotonic()

        def elapsed() -> int:
            return round((time.monotonic() - started) * 1000)

        def failed(outcome: Outcome, error: str, status: int | None = None) -> FetchResult:
            return FetchResult(
                url=url, outcome=outcome, status=status, error=error, elapsed_ms=elapsed()
            )

        context = self._ensure_browser().new_context(user_agent=self.user_agent)
        try:
            page = context.new_page()
            page.route("**/*", _block_heavy_resources)
            try:
                response = page.goto(
                    url, wait_until=_wait_until(wait_until), timeout=PAGE_TIMEOUT_MS
                )
            except PlaywrightTimeout:
                return failed(Outcome.RETRY, "timeout: page load")
            except Error as exc:  # net::ERR_CONNECTION_REFUSED, DNS failures, ...
                return failed(Outcome.RETRY, f"connection error: {exc.message.splitlines()[0]}")
            if response is None:
                return failed(Outcome.RETRY, "no response from page load")

            status = response.status
            headers = {name.lower(): value for name, value in response.headers.items()}
            early = classify_status(url, status, headers, elapsed())
            if early is not None:
                return early

            final_url = normalize_url(page.url)
            if final_url is not None and final_url != normalize_url(url):
                return FetchResult(
                    url=url,
                    outcome=Outcome.REDIRECT,
                    status=status,
                    headers=headers,
                    location=final_url,
                    elapsed_ms=elapsed(),
                )

            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=SELECTOR_TIMEOUT_MS)
                except PlaywrightTimeout:
                    return failed(Outcome.RETRY, f"timeout: waiting for {wait_for!r}", status)
            if settle_ms > 0:
                _settle(page, settle_ms)

            body = page.content()
            if len(body.encode("utf-8")) > MAX_BODY_BYTES:
                return failed(Outcome.PERMANENT, f"page larger than {MAX_BODY_BYTES} bytes", status)
            return FetchResult(
                url=url,
                outcome=Outcome.OK,
                status=status,
                headers=headers,
                body=body,
                elapsed_ms=elapsed(),
            )
        finally:
            context.close()


# How much is on the page: the number of elements and the length of the text.
_SIZE = "() => document.querySelectorAll('*').length + ':' + document.body.innerText.length"


def _settle(page: Page, quiet_ms: int) -> None:
    """Return once the page has not changed for ``quiet_ms`` (at most SETTLE_MAX_MS)."""
    waited = 0
    quiet = 0
    last = page.evaluate(_SIZE)
    while quiet < quiet_ms and waited < SETTLE_MAX_MS:
        page.wait_for_timeout(SETTLE_POLL_MS)
        waited += SETTLE_POLL_MS
        size = page.evaluate(_SIZE)
        quiet = quiet + SETTLE_POLL_MS if size == last else 0
        last = size


def _wait_until(value: str) -> WaitUntil:
    if value in ("load", "domcontentloaded", "networkidle", "commit"):
        return cast(WaitUntil, value)
    raise ValueError(f"unknown wait_until {value!r}")
