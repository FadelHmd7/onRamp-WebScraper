"""Worker heartbeats: a clean stop leaves at once, a killed worker expires within the TTL."""

import time

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from onramp_scraper.worker.heartbeat import KEY_PREFIX, Heartbeat, active_workers, wait_until


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_a_beat_publishes_the_worker_with_a_ttl(redis: fakeredis.FakeRedis) -> None:
    beat = Heartbeat(redis, "worker-1", ttl=15, interval=5)
    beat.beat()
    [worker] = active_workers(redis)
    assert worker["name"] == "worker-1"
    assert (worker["current_url"], worker["processed"]) == (None, 0)
    assert 14_000 < redis.pttl(KEY_PREFIX + "worker-1") <= 15_000


def test_current_job_and_count_are_reported(redis: fakeredis.FakeRedis) -> None:
    beat = Heartbeat(redis, "worker-1")
    beat.working_on("http://testsite:8000/items/1")
    assert active_workers(redis)[0]["current_url"] == "http://testsite:8000/items/1"
    beat.working_on(None)
    beat.working_on("http://testsite:8000/items/2")
    beat.working_on(None)
    assert active_workers(redis)[0]["processed"] == 2
    assert active_workers(redis)[0]["current_url"] is None


def test_clean_stop_leaves_at_once(redis: fakeredis.FakeRedis) -> None:
    beat = Heartbeat(redis, "worker-1", ttl=5, interval=1)
    beat.start()
    assert wait_until(lambda: len(active_workers(redis)) == 1, timeout=1.0)
    beat.stop(clean=True)
    assert active_workers(redis) == []


def test_killed_worker_drops_out_within_the_ttl(redis: fakeredis.FakeRedis) -> None:
    alive = Heartbeat(redis, "worker-1", ttl=0.6, interval=0.1)
    killed = Heartbeat(redis, "worker-2", ttl=0.6, interval=0.1)
    alive.start()
    killed.start()
    try:
        assert wait_until(lambda: len(active_workers(redis)) == 2, timeout=1.0)
        assert [w["name"] for w in active_workers(redis)] == ["worker-1", "worker-2"]
        time.sleep(1.0)  # longer than the TTL: beats keep both alive
        assert len(active_workers(redis)) == 2

        killed.stop(clean=False)  # no cleanup, like docker kill
        stopped = time.monotonic()
        assert len(active_workers(redis)) == 2  # not gone yet...
        assert wait_until(lambda: len(active_workers(redis)) == 1, timeout=2.0)
        assert time.monotonic() - stopped <= 0.6 + 0.2  # ...gone within the TTL
        assert active_workers(redis)[0]["name"] == "worker-1"
    finally:
        alive.stop()


def test_a_redis_outage_does_not_stop_the_heartbeat(redis: fakeredis.FakeRedis) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.failures = 2

        def set(self, *args: object, **kwargs: object) -> None:
            if self.failures:
                self.failures -= 1
                raise RedisConnectionError("down")
            redis.set(*args, **kwargs)  # type: ignore[arg-type]

        def delete(self, *args: object) -> None:
            redis.delete(*args)  # type: ignore[arg-type]

    beat = Heartbeat(Flaky(), "worker-1", ttl=1, interval=0.05)  # type: ignore[arg-type]
    beat.start()  # Redis is down at first: no exception, the worker still starts
    try:
        assert wait_until(lambda: len(active_workers(redis)) == 1, timeout=2.0)
    finally:
        beat.stop()


def test_interval_must_be_shorter_than_the_ttl(redis: fakeredis.FakeRedis) -> None:
    with pytest.raises(ValueError, match="shorter than the TTL"):
        Heartbeat(redis, "w", ttl=5, interval=5)
