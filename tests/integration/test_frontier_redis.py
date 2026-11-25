"""Shared seen-set and page budget against a real Redis server.

Runs only when REDIS_URL is set explicitly (CI sets it).
"""

import os
import threading
from collections.abc import Iterator

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from onramp_scraper.crawler.frontier import Frontier
from tests.unit.test_frontier import items, small_site

pytestmark = pytest.mark.integration

CRAWL = "frontier-integration"


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
    client.delete(f"crawl:{CRAWL}:seen", f"crawl:{CRAWL}:accepted")
    yield client
    client.delete(f"crawl:{CRAWL}:seen", f"crawl:{CRAWL}:accepted")


def test_racing_workers_in_real_redis(redis: Redis) -> None:
    site = small_site(max_pages=50)
    accepted: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker(offset: int) -> None:
        frontier = Frontier(redis)
        start.wait()
        for batch in range(10):
            got = frontier.discover(CRAWL, site, items(10, start=batch * 5 + offset), depth=0)
            with lock:
                accepted.extend(got)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(accepted) == len(set(accepted)) == 50
