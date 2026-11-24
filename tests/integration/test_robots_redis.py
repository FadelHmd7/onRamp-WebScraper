"""robots.txt cache against a real Redis server.

Runs only when REDIS_URL is set explicitly (CI sets it), so it never writes
to some other Redis that happens to listen on localhost.
"""

import os
from collections.abc import Iterator

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from onramp_scraper.crawler.robots import RobotsChecker, RobotsFile

pytestmark = pytest.mark.integration

HOST = "robots-integration.invalid"
UA = "onRamp-WebScraper/0.1 (integration test)"


@pytest.fixture
def redis() -> Iterator[Redis]:
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("set REDIS_URL to run against a real Redis")
    client = Redis.from_url(url)
    try:
        client.ping()
    except RedisConnectionError:
        pytest.skip("Redis is not reachable")
    yield client
    for key in client.scan_iter(f"robots:*{HOST}*"):
        client.delete(key)


def test_two_checkers_share_rules_through_real_redis(redis: Redis) -> None:
    calls: list[str] = []

    def fetch(url: str, user_agent: str) -> RobotsFile:
        calls.append(url)
        return RobotsFile(200, "User-agent: *\nDisallow: /private/\n")

    first = RobotsChecker(redis, UA, fetch=fetch)
    second = RobotsChecker(redis, UA, fetch=fetch)
    assert first.allowed(f"https://{HOST}/public") is True
    assert second.allowed(f"https://{HOST}/private/x") is False
    assert calls == [f"https://{HOST}/robots.txt"]
    assert redis.ttl(f"robots:https://{HOST}/robots.txt") > 0
