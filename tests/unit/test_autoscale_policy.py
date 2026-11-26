"""Autoscaling policy: up fast, down one at a time after a cooldown, within limits."""

import pytest

from onramp_scraper.autoscaler.policy import ScalePolicy, Scaler

POLICY = ScalePolicy(min_workers=1, max_workers=8, jobs_per_worker=25, cooldown=30)


@pytest.mark.parametrize(
    ("backlog", "workers"),
    [(0, 1), (1, 1), (25, 1), (26, 2), (100, 4), (101, 5), (10_000, 8)],
)
def test_workers_needed_for_a_backlog(backlog: int, workers: int) -> None:
    assert POLICY.needed(backlog) == workers


def test_a_growing_queue_scales_up_at_once() -> None:
    scaler = Scaler(POLICY)
    assert scaler.target(current=1, backlog=180, now=0) == 8
    assert scaler.target(current=2, backlog=60, now=1) == 3


def test_a_draining_queue_scales_down_one_at_a_time_after_the_cooldown() -> None:
    scaler = Scaler(POLICY)
    assert scaler.target(current=4, backlog=0, now=0) == 4  # low: start the cooldown
    assert scaler.target(current=4, backlog=0, now=29) == 4  # still cooling down
    assert scaler.target(current=4, backlog=0, now=30) == 3  # one step
    assert scaler.target(current=3, backlog=0, now=45) == 3  # the next step waits again
    assert scaler.target(current=3, backlog=0, now=60) == 2
    assert scaler.target(current=2, backlog=0, now=90) == 1
    assert scaler.target(current=1, backlog=0, now=500) == 1  # never below the minimum


def test_a_burst_during_the_cooldown_cancels_it() -> None:
    scaler = Scaler(POLICY)
    assert scaler.target(current=4, backlog=10, now=0) == 4
    assert scaler.target(current=4, backlog=100, now=20) == 4  # busy again: needs 4
    assert scaler.target(current=4, backlog=10, now=40) == 4  # the cooldown starts over
    assert scaler.target(current=4, backlog=10, now=69) == 4
    assert scaler.target(current=4, backlog=10, now=70) == 3


def test_limits_are_respected() -> None:
    scaler = Scaler(ScalePolicy(min_workers=2, max_workers=3, jobs_per_worker=10, cooldown=0))
    assert scaler.target(current=0, backlog=0, now=0) == 2  # brought up to the minimum
    assert scaler.target(current=2, backlog=1000, now=1) == 3  # capped at the maximum
    assert scaler.target(current=5, backlog=1000, now=2) == 3  # too many: back to the max


@pytest.mark.parametrize(
    "kwargs",
    [{"min_workers": 3, "max_workers": 2}, {"min_workers": -1}, {"jobs_per_worker": 0}],
)
def test_bad_limits_are_refused(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ScalePolicy(**kwargs)
