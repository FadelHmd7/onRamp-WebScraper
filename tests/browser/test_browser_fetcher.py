"""Real Chromium against the local test site, served live on a free port.

Skipped when Playwright or its Chromium is not installed. CI installs both.
"""

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from onramp_scraper.crawler.fetch import HttpFetcher, Outcome
from onramp_testsite import app as testsite
from onramp_testsite import content

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright.sync_api")

UA = "onRamp-WebScraper/0.1 (browser test)"


@pytest.fixture(scope="module")
def site_url() -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    testsite.LATENCY_SECONDS = 0
    server = uvicorn.Server(
        uvicorn.Config(testsite.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/health", timeout=0.5)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    from onramp_scraper.crawler.browser import BrowserFetcher

    fetcher = BrowserFetcher(UA)
    try:
        fetcher.fetch("about:blank")
    except Exception as exc:  # Chromium not installed
        pytest.skip(f"Chromium is not available: {exc}")
    yield fetcher
    fetcher.close()


def test_browser_sees_javascript_content_that_http_does_not(site_url: str, browser) -> None:
    title = content.item(7).title
    url = f"{site_url}/js/items/7"

    plain = HttpFetcher(UA).fetch(url)
    assert plain.outcome is Outcome.OK
    assert plain.body is not None and title not in plain.body

    rendered = browser.fetch(url, wait_until="networkidle", wait_for="article.item h1")
    assert rendered.outcome is Outcome.OK
    assert rendered.status == 200
    assert rendered.body is not None and title in rendered.body
    assert "etag" in rendered.headers


def test_missing_element_is_retried_not_stored(site_url: str, browser) -> None:
    result = browser.fetch(f"{site_url}/items/1", wait_for="#never-appears")
    assert result.outcome is Outcome.RETRY
    assert result.error is not None and "never-appears" in result.error


def test_browser_uses_the_same_status_rules(site_url: str, browser) -> None:
    assert browser.fetch(f"{site_url}/items/99999").outcome is Outcome.PERMANENT  # 404
    broken = browser.fetch(f"{site_url}/broken/1")
    assert broken.outcome is Outcome.RETRY  # 503
    assert broken.status == 503


def test_unreachable_host_is_retryable(browser) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    result = browser.fetch(f"http://127.0.0.1:{closed_port}/")
    assert result.outcome is Outcome.RETRY
    assert result.error is not None and "connection error" in result.error


def test_user_agent_is_sent(site_url: str, browser) -> None:
    result = browser.fetch(f"{site_url}/whoami", wait_for="#user-agent")
    assert result.outcome is Outcome.OK
    assert result.body is not None and f'<p id="user-agent">{UA}</p>' in result.body


def test_settle_waits_for_sections_that_arrive_later(site_url: str, browser) -> None:
    """Like Inventaire: the item appears first, a second section 1.5 s later."""
    url = f"{site_url}/js/items/3?late_ms=1500"
    hurried = browser.fetch(url, wait_until="networkidle", wait_for="article.item h1")
    assert hurried.outcome is Outcome.OK
    assert 'id="late"' not in (hurried.body or "")  # saved before it arrived

    settled = browser.fetch(
        url, wait_until="networkidle", wait_for="article.item h1", settle_ms=2000
    )
    assert 'id="late"' in (settled.body or "")
    assert settled.elapsed_ms >= 1500


def test_settle_gives_up_on_a_page_that_never_stops_changing(
    site_url: str, browser, monkeypatch
) -> None:
    from onramp_scraper.crawler import browser as browser_module

    monkeypatch.setattr(browser_module, "SETTLE_MAX_MS", 1000)
    result = browser.fetch(
        f"{site_url}/js/items/3?late_ms=5000", wait_for="article.item h1", settle_ms=3000
    )
    assert result.outcome is Outcome.OK
    assert result.elapsed_ms < 4000  # capped, not 3 s of quiet after a 5 s change
