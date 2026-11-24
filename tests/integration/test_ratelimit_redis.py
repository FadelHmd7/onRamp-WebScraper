"""Shared rate limit against a real Redis server.

Runs only when REDIS_URL is set explicitly (CI sets it).
"""

import os
from collections.abc import Iterator

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.unit.test_ratelimit import assert_respects_rate, run_workers

pytestmark = pytest.mark.integration

HOST = "ratelimit-integration.invalid"


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
    client.delete(f"ratelimit:{HOST}")
    yield client
    client.delete(f"ratelimit:{HOST}")


def test_eight_workers_share_one_limit_in_real_redis(redis: Redis) -> None:
    rate = 20.0
    times = run_workers(redis, HOST, rate, workers=8, each=5)
    assert len(times) == 40
    assert_respects_rate(times, rate)
