"""HTTP fetching: every response becomes a classified result; politeness comes first."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import httpx
import pytest

from onramp_scraper.crawler.fetch import (
    MAX_BODY_BYTES,
    MAX_RETRY_AFTER,
    HttpFetcher,
    Outcome,
    PoliteFetcher,
    parse_retry_after,
)
from onramp_scraper.crawler.ratelimit import RateLimiter
from onramp_scraper.crawler.robots import RobotsChecker, RobotsFile

FIXTURES = Path(__file__).parents[1] / "fixtures"
UA = "onRamp-WebScraper/0.1 (test)"
BOOK = (FIXTURES / "books-toscrape" / "book-a-light-in-the-attic.html").read_text("utf-8")
HTML = {"content-type": "text/html; charset=utf-8"}

Handler = Callable[[httpx.Request], httpx.Response]


def fetcher(handler: Handler) -> HttpFetcher:
    return HttpFetcher(UA, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- outcomes


def test_ok_returns_body_and_cache_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {**HTML, "ETag": '"abc"', "Last-Modified": "Wed, 08 Feb 2023 21:02:32 GMT"}
        return httpx.Response(200, headers=headers, text=BOOK)

    result = fetcher(handler).fetch("https://books.toscrape.com/catalogue/x/index.html")
    assert result.outcome is Outcome.OK
    assert result.status == 200
    assert result.body is not None and "A Light in the Attic" in result.body
    assert result.etag == '"abc"'
    assert result.last_modified == "Wed, 08 Feb 2023 21:02:32 GMT"
    assert result.fetched_at.tzinfo is UTC


def test_sends_user_agent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, headers=HTML, text="<p>x</p>")

    fetcher(handler).fetch("https://a.example/")
    assert seen["user-agent"] == UA


def test_conditional_request_and_304() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    result = fetcher(handler).fetch(
        "https://a.example/", etag='"v1"', last_modified="Wed, 08 Feb 2023 21:02:32 GMT"
    )
    assert seen["if-none-match"] == '"v1"'
    assert seen["if-modified-since"] == "Wed, 08 Feb 2023 21:02:32 GMT"
    assert result.outcome is Outcome.NOT_MODIFIED
    assert result.body is None


def test_no_conditional_headers_on_first_crawl() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, headers=HTML, text="x")

    fetcher(handler).fetch("https://a.example/")
    assert "if-none-match" not in seen and "if-modified-since" not in seen


@pytest.mark.parametrize("status", [429, 503, 500, 502, 504, 408])
def test_temporary_errors_are_retryable(status: int) -> None:
    result = fetcher(lambda r: httpx.Response(status)).fetch("https://a.example/")
    assert result.outcome is Outcome.RETRY
    assert result.error == f"HTTP {status}"


def test_429_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    result = fetcher(handler).fetch("https://a.example/")
    assert result.outcome is Outcome.RETRY
    assert result.retry_after == 7.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_client_errors_are_permanent(status: int) -> None:
    result = fetcher(lambda r: httpx.Response(status)).fetch("https://a.example/")
    assert result.outcome is Outcome.PERMANENT


def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = fetcher(handler).fetch("https://a.example/")
    assert result.outcome is Outcome.RETRY
    assert result.status is None
    assert result.error is not None and "timeout" in result.error


def test_connection_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = fetcher(handler).fetch("https://a.example/")
    assert result.outcome is Outcome.RETRY
    assert result.error is not None and "connection error" in result.error


def test_redirect_is_returned_not_followed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(301, headers={"Location": "../new/page.html#top"})

    result = fetcher(handler).fetch("https://a.example/old/sub/page.html")
    assert result.outcome is Outcome.REDIRECT
    assert result.location == "https://a.example/old/new/page.html"
    assert calls == ["https://a.example/old/sub/page.html"]  # only one request


def test_redirect_without_location_is_permanent() -> None:
    result = fetcher(lambda r: httpx.Response(302)).fetch("https://a.example/")
    assert result.outcome is Outcome.PERMANENT


def test_non_html_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    result = fetcher(handler).fetch("https://a.example/file")
    assert result.outcome is Outcome.PERMANENT
    assert result.error == "not HTML: application/pdf"


def test_oversized_page_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=HTML, content=b"x" * (MAX_BODY_BYTES + 1))

    result = fetcher(handler).fetch("https://a.example/huge")
    assert result.outcome is Outcome.PERMANENT
    assert result.body is None


# ---------------------------------------------------------------- Retry-After


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5", 5.0),
        ("0", 0.0),
        ("999999", MAX_RETRY_AFTER),
        ("Wed, 25 Nov 2026 12:00:30 GMT", 30.0),
        ("Wed, 25 Nov 2026 11:59:00 GMT", 0.0),  # already in the past
        ("soon", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after(value, now=now) == expected


# ---------------------------------------------------------------- politeness


class RecordingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(fakeredis.FakeRedis(), sleep=lambda s: None)
        self.waits: list[tuple[str, float]] = []
        self.pauses: list[tuple[str, float]] = []

    def wait(self, host: str, requests_per_second: float, burst: int = 1) -> float:
        self.waits.append((host, requests_per_second))
        return 0.0

    def pause(self, host: str, seconds: float) -> None:
        self.pauses.append((host, seconds))


def polite(handler: Handler, robots: RobotsFile) -> tuple[PoliteFetcher, RecordingLimiter]:
    limiter = RecordingLimiter()
    checker = RobotsChecker(fakeredis.FakeRedis(), UA, fetch=lambda url, ua: robots)
    return PoliteFetcher(fetcher(handler), checker, limiter), limiter


INVENTAIRE_ROBOTS = RobotsFile(200, (FIXTURES / "inventaire" / "robots.txt").read_text("utf-8"))


def test_robots_disallowed_url_is_never_requested() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, headers=HTML, text="x")

    polite_fetcher, limiter = polite(handler, INVENTAIRE_ROBOTS)
    result = polite_fetcher.fetch("https://inventaire.io/search?q=x", requests_per_second=1)
    assert result.outcome is Outcome.BLOCKED
    assert calls == []
    assert limiter.waits == []  # no slot used either


def test_crawl_delay_lowers_the_rate() -> None:
    polite_fetcher, limiter = polite(
        lambda r: httpx.Response(200, headers=HTML, text="x"), INVENTAIRE_ROBOTS
    )
    polite_fetcher.fetch("https://inventaire.io/entity/wd:Q1", requests_per_second=1)
    assert limiter.waits == [("inventaire.io", pytest.approx(0.1))]


def test_429_pauses_the_whole_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    polite_fetcher, limiter = polite(handler, RobotsFile(404))
    result = polite_fetcher.fetch("https://a.example/x", requests_per_second=1)
    assert result.outcome is Outcome.RETRY
    assert limiter.pauses == [("a.example", 30.0)]


def test_plain_500_does_not_pause_the_host() -> None:
    polite_fetcher, limiter = polite(lambda r: httpx.Response(500), RobotsFile(404))
    polite_fetcher.fetch("https://a.example/x", requests_per_second=1)
    assert limiter.pauses == []


# ---------------------------------------------------------------- limiter pause


def test_pause_holds_back_every_worker() -> None:
    redis = fakeredis.FakeRedis()
    worker_a, worker_b = RateLimiter(redis), RateLimiter(redis)
    worker_a.pause("a.example", 5.0)
    assert worker_b.reserve("a.example", requests_per_second=10) == pytest.approx(5.0, abs=0.05)


def test_pause_never_shortens_a_longer_wait() -> None:
    redis = fakeredis.FakeRedis()
    limiter = RateLimiter(redis)
    limiter.pause("a.example", 10.0)
    limiter.pause("a.example", 2.0)
    assert limiter.reserve("a.example", requests_per_second=10) == pytest.approx(10.0, abs=0.05)


def test_redirect_to_itself_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "https://A.example:443/#x"})

    assert fetcher(handler).fetch("https://a.example/").outcome is Outcome.PERMANENT
