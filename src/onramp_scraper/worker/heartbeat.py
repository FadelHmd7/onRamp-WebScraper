"""Worker heartbeats: who is alive, and what is each worker doing.

Every worker process keeps one Redis key, ``worker:heartbeat:<name>``, with a
short TTL, and a background thread refreshes it every few seconds. The key
holds what the worker is doing (current URL, jobs done), for the dashboard.

- A worker that stops normally deletes its key: it disappears at once.
- A worker that is killed (``docker kill``, a crash, a lost machine) cannot
  delete anything, so its key simply expires: the count of active workers
  drops within ``ttl`` seconds without anyone having to notice the death.

Counting keys is cheaper and more honest than asking Celery: an inspect
broadcast waits for replies, and a hung worker still holds its connection.
"""

import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

KEY_PREFIX = "worker:heartbeat:"


class Heartbeat:
    def __init__(
        self,
        redis: Redis,
        name: str | None = None,
        ttl: float = 15.0,
        interval: float = 5.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if interval >= ttl:
            raise ValueError("the interval must be shorter than the TTL")
        self.redis = redis
        # One worker process per container, so the hostname names it. A
        # replaced child process reuses the key instead of counting twice.
        self.name = name or socket.gethostname()
        self.ttl = ttl
        self.interval = interval
        self.clock = clock
        self.started_at = clock()
        self.current_url: str | None = None
        self.processed = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def key(self) -> str:
        return KEY_PREFIX + self.name

    def beat(self) -> None:
        state = {
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "last_beat": self.clock().isoformat(),
            "current_url": self.current_url,
            "processed": self.processed,
        }
        self.redis.set(self.key, json.dumps(state), px=int(self.ttl * 1000))

    def _run(self) -> None:
        while not self._stop.is_set():
            # Redis is down: the key expires, which is the truth; try again next beat.
            with contextlib.suppress(RedisError):
                self.beat()
            self._stop.wait(self.interval)

    def start(self) -> None:
        """Beat now and every interval, in a background thread. Never raises:
        a worker that starts while Redis is down still runs jobs, and shows up
        as soon as Redis is back."""
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self, *, clean: bool = True) -> None:
        """Stop beating. ``clean`` deletes the key; without it the key expires (a crash)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1)
        if clean:
            with contextlib.suppress(RedisError):
                self.redis.delete(self.key)

    def working_on(self, url: str | None) -> None:
        """Record the job in hand (None when it is done) and beat at once."""
        if url is None and self.current_url is not None:
            self.processed += 1
        self.current_url = url
        with contextlib.suppress(RedisError):
            self.beat()


def active_workers(redis: Redis) -> list[dict[str, Any]]:
    """Every worker with a live heartbeat, sorted by name."""
    workers = []
    for key in redis.scan_iter(match=KEY_PREFIX + "*", count=100):
        raw = redis.get(key)
        if raw is not None:  # it may expire between SCAN and GET
            workers.append(json.loads(raw))
    return sorted(workers, key=lambda w: str(w["name"]))


def wait_until(condition: Callable[[], bool], timeout: float, step: float = 0.1) -> bool:
    """Poll ``condition`` until it holds or ``timeout`` passes (for tests and demos)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()
