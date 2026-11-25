"""PoliteFetcher picks HTTP or the browser from the site's fetch_mode."""

import fakeredis
import httpx

from onramp_scraper.crawler.fetch import FetchResult, HttpFetcher, Outcome, PoliteFetcher
from onramp_scraper.crawler.ratelimit import RateLimiter
from onramp_scraper.crawler.robots import RobotsChecker, RobotsFile
from tests.unit.test_fetch import site

UA = "onRamp-WebScraper/0.1 (test)"


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def fetch(self, url: str, wait_until: str, wait_for: str | None) -> FetchResult:
        self.calls.append((url, wait_until, wait_for))
        return FetchResult(url=url, outcome=Outcome.OK, status=200, body="<p>rendered</p>")


def make(http_calls: list[str]) -> tuple[PoliteFetcher, list[FakeBrowser]]:
    def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>static</p>")

    created: list[FakeBrowser] = []

    def factory() -> FakeBrowser:
        created.append(FakeBrowser())
        return created[-1]

    redis = fakeredis.FakeRedis()
    polite = PoliteFetcher(
        HttpFetcher(UA, transport=httpx.MockTransport(handler)),
        RobotsChecker(redis, UA, fetch=lambda url, ua: RobotsFile(404)),
        RateLimiter(redis, sleep=lambda s: None),
        browser_factory=factory,
    )
    return polite, created


def test_static_site_never_starts_a_browser() -> None:
    http_calls: list[str] = []
    polite, created = make(http_calls)
    for n in range(3):
        result = polite.fetch(f"https://a.example/{n}", site("a.example", rate=100))
        assert result.body == "<p>static</p>"
    assert created == []
    assert len(http_calls) == 3


def test_browser_site_uses_one_browser_with_its_wait_rules() -> None:
    http_calls: list[str] = []
    polite, created = make(http_calls)
    js_site = site(
        "b.example",
        rate=100,
        fetch_mode="browser",
        browser_wait_until="networkidle",
        browser_wait_for=".top-section h2",
    )
    polite.fetch("https://b.example/1", js_site)
    polite.fetch("https://b.example/2", js_site)
    assert len(created) == 1  # started once, then reused
    assert created[0].calls == [
        ("https://b.example/1", "networkidle", ".top-section h2"),
        ("https://b.example/2", "networkidle", ".top-section h2"),
    ]
    assert http_calls == []
