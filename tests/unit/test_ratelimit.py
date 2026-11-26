"""Shared per-domain rate limit: N workers together never exceed the limit."""

import itertools
import threading
import time
from dataclasses import dataclass

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
                sent.append(time.perf_counter())

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


@dataclass(frozen=True)
class Slot:
    """When a reserved request may go. Redis reads its clock *during* the call, so
    the true slot lies between ``earliest`` (clock before the call + wait) and
    ``latest`` (clock after the call + wait). Bracketing makes the timing tests
    exact even when the machine is busy and a call takes a while.

    perf_counter, not monotonic: on Windows monotonic only ticks every ~15.6 ms."""

    wait: float
    earliest: float
    latest: float


def slot(limiter: RateLimiter, host: str, rate: float, burst: int = 1) -> Slot:
    before = time.perf_counter()
    wait = limiter.reserve(host, rate, burst)
    after = time.perf_counter()
    return Slot(wait, before + wait, after + wait)


def assert_gap(first: Slot, second: Slot, seconds: float, slack: float = 0.005) -> None:
    """The gap between two slots can be ``seconds``, given what we could measure."""
    assert second.earliest - first.latest <= seconds + slack
    assert second.latest - first.earliest >= seconds - slack


def assert_resumes_after(paused: tuple[float, float], resumed: Slot, seconds: float) -> None:
    """``paused`` is (clock before, clock after) the pause call."""
    assert resumed.earliest - paused[1] <= seconds + 0.005
    assert resumed.latest - paused[0] >= seconds - 0.005


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_eight_workers_share_one_limit(redis: fakeredis.FakeRedis) -> None:
    """The spec's example, sped up: 8 workers at 5 req/s total, not 8 x 5.

    5 req/s (200 ms apart) rather than faster: the test times when each thread
    actually wakes up, and on a busy machine a thread can wake tens of
    milliseconds late. The spacing must be large next to that delay, or the
    test would measure the operating system instead of the limiter."""
    rate = 5.0
    times = run_workers(redis, "books.toscrape.com", rate, workers=8, each=3)
    assert len(times) == 24
    assert_respects_rate(times, rate)


def test_slots_are_handed_out_in_order(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    taken = [slot(limiter, "a.example", 2) for _ in range(4)]
    assert taken[0].wait == 0.0  # a fresh host: the first request goes at once
    for earlier, later in itertools.pairwise(taken):
        assert_gap(earlier, later, 0.5)


def test_separate_limiters_share_state(redis: fakeredis.FakeRedis) -> None:
    worker_a, worker_b = RateLimiter(redis), RateLimiter(redis)
    first = slot(worker_a, "a.example", 1)
    second = slot(worker_b, "a.example", 1)
    assert first.wait == 0.0
    assert_gap(first, second, 1.0)


def test_hosts_are_independent(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    limiter.reserve("a.example", 1)
    assert limiter.reserve("b.example", 1) == 0.0


def test_burst_allows_back_to_back_requests_then_spaces(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    taken = [slot(limiter, "a.example", 1, burst=3) for _ in range(4)]
    assert [s.wait for s in taken[:3]] == [0.0, 0.0, 0.0]
    assert_gap(taken[0], taken[3], 1.0)


def test_idle_host_starts_fresh(redis: fakeredis.FakeRedis) -> None:
    limiter = RateLimiter(redis)
    limiter.reserve("a.example", requests_per_second=20)
    time.sleep(0.1)  # longer than one 0.05 s interval
    assert limiter.reserve("a.example", requests_per_second=20) == 0.0


def test_key_expires_when_idle(redis: fakeredis.FakeRedis) -> None:
    RateLimiter(redis).reserve("a.example", requests_per_second=1)
    ttl_ms = redis.pttl("ratelimit:a.example")
    assert 0 < ttl_ms <= 2100  # one interval plus one second of margin


def test_wait_sleeps_for_the_reserved_delay(redis: fakeredis.FakeRedis) -> None:
    slept: list[float] = []
    limiter = RateLimiter(redis, sleep=slept.append)
    before_first = time.perf_counter()
    limiter.wait("a.example", 1)  # first slot: no wait, no sleep
    after_first = time.perf_counter()
    limiter.wait("a.example", 1)
    after_second = time.perf_counter()
    assert len(slept) == 1
    first = Slot(0.0, before_first, after_first)
    second = Slot(slept[0], after_first + slept[0], after_second + slept[0])
    assert_gap(first, second, 1.0)


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
