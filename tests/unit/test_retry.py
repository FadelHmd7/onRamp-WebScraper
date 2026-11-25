"""Retry policy: exponential backoff with full jitter, Retry-After as a floor."""

import random

import pytest

from onramp_scraper.worker.retry import RetryPolicy

POLICY = RetryPolicy(max_attempts=5, base_delay=2.0, max_delay=60.0)


def test_retries_until_the_last_attempt() -> None:
    assert [POLICY.should_retry(n) for n in range(1, 7)] == [True, True, True, True, False, False]
    assert not RetryPolicy(max_attempts=1).should_retry(1)  # one attempt: no retries


@pytest.mark.parametrize(
    ("attempt", "ceiling"), [(1, 2.0), (2, 4.0), (3, 8.0), (5, 32.0), (9, 60.0)]
)
def test_delay_stays_under_the_doubling_ceiling(attempt: int, ceiling: float) -> None:
    rng = random.Random(attempt)  # noqa: S311 (seeded test data)
    delays = [POLICY.delay(attempt, rng=rng) for _ in range(500)]
    assert all(0 <= d <= ceiling for d in delays)
    assert max(delays) > ceiling * 0.9  # the whole range is used...
    assert min(delays) < ceiling * 0.1  # ...which is what spreads retries out


def test_jitter_spreads_simultaneous_failures() -> None:
    rng = random.Random(7)  # noqa: S311 (seeded test data)
    delays = [POLICY.delay(3, rng=rng) for _ in range(100)]
    assert len({round(d, 2) for d in delays}) > 90


def test_retry_after_is_a_floor() -> None:
    rng = random.Random(1)  # noqa: S311 (seeded test data)
    assert all(POLICY.delay(1, retry_after=30.0, rng=rng) >= 30.0 for _ in range(100))
    # ... capped, so a hostile Retry-After cannot park a URL for a day.
    assert POLICY.delay(1, retry_after=86_400.0, rng=rng) == 60.0
