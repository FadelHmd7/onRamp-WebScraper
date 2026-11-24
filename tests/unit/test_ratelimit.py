"""Shared per-domain rate limit: N workers together never exceed the limit."""

import itertools
import threading
import time

import fakeredis
import pytest
from redis import Redis

from onramp_scraper.crawler.ratelimit import RateLimiter, effective_rate


def run_workers(redis: Redis, host: str, rate: float, workers: int, each: int) -> list[float]:
    """Each thread is one worker with its own limiter; returns when each request went out."""
    sent: list[float] = []
    lock = threading.Lock()
    start = threading.Barrier(workers)

    def worker() -> None:
        limiter = RateLimiter(redis)  # separate instance, like a separate container
        start.wait()
        for _ in range(each):
            limiter.wait(host, rate)
            with lock:
                sent.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(sent)


def max_in_window(times: list[float], window: float) -> int:
    best, left = 0, 0
    for right, t in enumerate(times):
        while t - times[left] >= window:
            left += 1
        best = max(best, right - left + 1)
    return best


def assert_respects_rate(times: list[float], rate: float) -> None:
    # Over the whole run: (n - 1) intervals must take at least (n - 1) / rate seconds.
    elapsed = times[-1] - times[0]
    assert elapsed >= (len(times) - 1) / rate * 0.95, f"too fast: {elapsed:.2f}s"
    # In any 1-second window: no more than the limit (plus one for the window edge).
    assert max_in_window(times, 1.0) <= rate + 1


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_eight_workers_share_one_limit(redis: fakeredis.FakeRedis) -> None:
    """The spec's example, sped up: 8 workers at 20 req/s total, not 8 x 20."""
    rate = 20.0
    times = run_workers(redis, "books.toscrape.com", rate, workers=8, each=5)
    assert len(times) == 40
    assert_respects_rate(times, rate)


def test_slots_are_handed_out_in_order(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    waits = [limiter.reserve("a.example", requests_per_second=2) for _ in range(4)]
    assert waits[0] == pytest.approx(0.0, abs=0.05)
    for earlier, later in itertools.pairwise(waits):
        assert later - earlier == pytest.approx(0.5, abs=0.05)


def test_separate_limiters_share_state(redis: fakeredis.FakeRedis) -> None:
    worker_a, worker_b = RateLimiter(redis), RateLimiter(redis)
    assert worker_a.reserve("a.example", 1) == pytest.approx(0.0, abs=0.05)
    assert worker_b.reserve("a.example", 1) == pytest.approx(1.0, abs=0.05)


def test_hosts_are_independent(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    limiter.reserve("a.example", 1)
    assert limiter.reserve("b.example", 1) == pytest.approx(0.0, abs=0.05)


def test_burst_allows_back_to_back_requests_then_spaces(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    waits = [limiter.reserve("a.example", requests_per_second=1, burst=3) for _ in range(4)]
    assert waits[:3] == pytest.approx([0.0, 0.0, 0.0], abs=0.05)
    assert waits[3] == pytest.approx(1.0, abs=0.05)


def test_idle_host_starts_fresh(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    limiter.reserve("a.example", requests_per_second=20)
    time.sleep(0.1)  # longer than one 0.05 s interval
    assert limiter.reserve("a.example", requests_per_second=20) == pytest.approx(0.0, abs=0.02)


def test_key_expires_when_idle(redis: fakeredis.FakeRedis) -> None:
    RateLimiter(redis).reserve("a.example", requests_per_second=1)
    ttl_ms = redis.pttl("ratelimit:a.example")
    assert 0 < ttl_ms <= 2100  # one interval plus one second of margin


def test_wait_sleeps_for_the_reserved_delay(redis: fakeredis.FakeRedis) -> None:
    slept: list[float] = []
    limiter = RateLimiter(redis, sleep=slept.append)
    limiter.wait("a.example", 1)  # first slot: no wait, no sleep
    limiter.wait("a.example", 1)
    assert len(slept) == 1
    assert slept[0] == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize(("rate", "burst"), [(0, 1), (-1, 1), (1, 0)])
def test_rejects_invalid_settings(redis: fakeredis.FakeRedis, rate: float, burst: int) -> None:
    with pytest.raises(ValueError):
        RateLimiter(redis).reserve("a.example", rate, burst)


@pytest.mark.parametrize(
    ("configured", "crawl_delay", "expected"),
    [
        (1.0, None, 1.0),
        (1.0, 10.0, 0.1),  # Inventaire: Crawl-delay 10 wins
        (0.05, 10.0, 0.05),  # our own limit is already slower
        (2.0, 0.0, 2.0),
    ],
)
def test_effective_rate(configured: float, crawl_delay: float | None, expected: float) -> None:
    assert effective_rate(configured, crawl_delay) == pytest.approx(expected)
