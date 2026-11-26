"""Required test area 2: robots.txt checks, against saved robots.txt files."""

import threading
from pathlib import Path

import fakeredis
import pytest

from onramp_scraper.crawler import robots as robots_module
from onramp_scraper.crawler.robots import RobotsChecker, RobotsFile, RobotsPolicy, robots_url

FIXTURES = Path(__file__).parents[1] / "fixtures"
UA = "onRamp-WebScraper/0.1 (+https://github.com/FadelHmd7/onRamp-WebScraper; contact: x@y.z)"


def saved(site: str) -> RobotsFile:
    return RobotsFile(200, (FIXTURES / site / "robots.txt").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- rules from real files


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://inventaire.io/entity/wd:Q36322", True),
        ("https://inventaire.io/entity/wd:Q36322/edit", False),  # Disallow: /entity/*/*$
        ("https://inventaire.io/entity/new", False),
        ("https://inventaire.io/search?q=austen", False),
        ("https://inventaire.io/items/abc", False),
        ("https://inventaire.io/api/entities?action=by-uris", True),  # data API used by the app
    ],
)
def test_inventaire_rules(url: str, allowed: bool) -> None:
    assert RobotsPolicy(saved("inventaire")).can_fetch(url, UA) is allowed


def test_inventaire_crawl_delay() -> None:
    assert RobotsPolicy(saved("inventaire")).crawl_delay(UA) == 10.0


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://standardebooks.org/ebooks?page=2", True),
        ("https://standardebooks.org/ebooks/jane-austen/emma", True),
        ("https://standardebooks.org/honeypot", False),
        # Only the named SEO/AI bot group disallows downloads; "*" does not.
        ("https://standardebooks.org/ebooks/jane-austen/emma/downloads/emma.epub", True),
    ],
)
def test_standard_ebooks_rules_for_our_agent(url: str, allowed: bool) -> None:
    assert RobotsPolicy(saved("standard-ebooks")).can_fetch(url, UA) is allowed


def test_standard_ebooks_named_bots_get_their_own_group() -> None:
    policy = RobotsPolicy(saved("standard-ebooks"))
    url = "https://standardebooks.org/ebooks/jane-austen/emma/downloads/emma.epub"
    assert policy.can_fetch(url, "AhrefsBot") is False
    assert policy.can_fetch(url, UA) is True


def test_wildcard_and_longest_match() -> None:
    policy = RobotsPolicy(
        RobotsFile(
            200,
            "User-agent: *\nDisallow: /private/\nAllow: /private/open/\nDisallow: /*.pdf$\n",
        )
    )
    assert policy.can_fetch("https://a.example/private/x", UA) is False
    assert policy.can_fetch("https://a.example/private/open/x", UA) is True  # longer rule wins
    assert policy.can_fetch("https://a.example/docs/file.pdf", UA) is False
    assert policy.can_fetch("https://a.example/docs/file.pdf?v=2", UA) is True  # "$" anchors


def test_testsite_private_is_disallowed() -> None:
    from onramp_testsite.app import ROBOTS_TXT

    policy = RobotsPolicy(RobotsFile(200, ROBOTS_TXT))
    assert policy.can_fetch("http://testsite:8000/private/secret", UA) is False
    assert policy.can_fetch("http://testsite:8000/items/1", UA) is True


# ---------------------------------------------------------------- fetch status meaning


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (404, True),  # unavailable: no rules, allow all (books.toscrape.com)
        (403, True),
        (500, False),  # unreachable: disallow all
        (503, False),
        (0, False),  # network error
    ],
)
def test_status_without_rules(status: int, allowed: bool) -> None:
    policy = RobotsPolicy(RobotsFile(status))
    assert policy.can_fetch("https://a.example/anything", UA) is allowed
    assert policy.crawl_delay(UA) is None


def test_robots_txt_itself_is_always_fetchable() -> None:
    policy = RobotsPolicy(RobotsFile(200, "User-agent: *\nDisallow: /\n"))
    assert policy.can_fetch("https://a.example/robots.txt", UA) is True
    assert policy.can_fetch("https://a.example/", UA) is False


def test_robots_url_keeps_scheme_and_port() -> None:
    assert robots_url("http://testsite:8000/items/1?x=1") == "http://testsite:8000/robots.txt"
    assert robots_url("https://inventaire.io/entity/wd:Q1") == "https://inventaire.io/robots.txt"


# ---------------------------------------------------------------- shared cache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingFetch:
    def __init__(self, result: RobotsFile) -> None:
        self.result = result
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, url: str, user_agent: str) -> RobotsFile:
        with self.lock:
            self.calls.append(url)
        return self.result


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_two_workers_share_one_fetch(redis: fakeredis.FakeRedis) -> None:
    fetch = CountingFetch(saved("inventaire"))
    worker_a = RobotsChecker(redis, UA, fetch=fetch)
    worker_b = RobotsChecker(redis, UA, fetch=fetch)  # separate process, same Redis
    assert worker_a.allowed("https://inventaire.io/entity/wd:Q1") is True
    assert worker_b.allowed("https://inventaire.io/entity/wd:Q1/edit") is False
    assert fetch.calls == ["https://inventaire.io/robots.txt"]


def test_concurrent_workers_fetch_once(redis: fakeredis.FakeRedis) -> None:
    fetch = CountingFetch(saved("inventaire"))
    checkers = [RobotsChecker(redis, UA, fetch=fetch) for _ in range(8)]
    threads = [
        threading.Thread(target=c.allowed, args=("https://inventaire.io/entity/wd:Q1",))
        for c in checkers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(fetch.calls) == 1


def test_each_host_has_its_own_rules(redis: fakeredis.FakeRedis) -> None:
    def fetch(url: str, user_agent: str) -> RobotsFile:
        return saved("inventaire") if "inventaire" in url else RobotsFile(404)

    checker = RobotsChecker(redis, UA, fetch=fetch)
    assert checker.allowed("https://inventaire.io/search") is False
    assert checker.allowed("https://books.toscrape.com/search") is True


def test_unreachable_result_expires_quickly(redis: fakeredis.FakeRedis) -> None:
    fetch = CountingFetch(RobotsFile(503))
    clock = FakeClock()
    checker = RobotsChecker(redis, UA, fetch=fetch, clock=clock)
    assert checker.allowed("https://a.example/x") is False
    ttl = redis.ttl("robots:https://a.example/robots.txt")
    assert 0 < ttl <= robots_module.ERROR_TTL


def test_good_result_is_cached_for_a_day(redis: fakeredis.FakeRedis) -> None:
    checker = RobotsChecker(redis, UA, fetch=CountingFetch(saved("inventaire")))
    checker.allowed("https://inventaire.io/entity/wd:Q1")
    ttl = redis.ttl("robots:https://inventaire.io/robots.txt")
    assert robots_module.ERROR_TTL < ttl <= robots_module.CACHE_TTL


def test_local_copy_is_refreshed_from_redis(redis: fakeredis.FakeRedis) -> None:
    clock = FakeClock()
    checker = RobotsChecker(redis, UA, fetch=CountingFetch(RobotsFile(404)), clock=clock)
    assert checker.allowed("https://a.example/x") is True
    # Another worker stores stricter rules; this worker sees them after LOCAL_TTL.
    redis.set(
        "robots:https://a.example/robots.txt",
        '{"status": 200, "body": "User-agent: *\\nDisallow: /\\n"}',
    )
    assert checker.allowed("https://a.example/x") is True  # still the local copy
    clock.now += robots_module.LOCAL_TTL + 1
    assert checker.allowed("https://a.example/x") is False
