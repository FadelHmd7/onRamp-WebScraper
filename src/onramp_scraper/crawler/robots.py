"""robots.txt checks, shared by every worker through Redis.

What a fetch result means (RFC 9309, section 2.3.1):

- 2xx: parse the file and follow its rules.
- 4xx (including 404): the file is "unavailable", so everything is allowed.
- 5xx or a network error: the file is "unreachable", so everything is
  disallowed until a later fetch succeeds. We never guess while a server is down.

The parsed rules come from protego, which supports the ``*`` and ``$``
wildcards and longest-match precedence. Python's ``urllib.robotparser`` does
not, and would misread rules such as Inventaire's ``Disallow: /entity/*/*$``.

One worker fetches a host's robots.txt and stores it in Redis; the others
reuse it. A short Redis lock stops several workers from fetching the same
file at the same moment.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from protego import Protego
from redis import Redis

MAX_BYTES = 500 * 1024  # RFC 9309: parse at least the first 500 KiB
MAX_REDIRECTS = 5  # RFC 9309: follow at least five redirects
CACHE_TTL = 24 * 3600  # seconds a good or 4xx result is reused
ERROR_TTL = 5 * 60  # seconds an unreachable result is reused before retrying
LOCAL_TTL = 60  # seconds a worker keeps its own copy before asking Redis again
LOCK_TTL = 30
LOCK_WAIT = 10.0


@dataclass(frozen=True)
class RobotsFile:
    """A fetched robots.txt. ``status`` 0 means the request itself failed."""

    status: int
    body: str = ""


class RobotsPolicy:
    def __init__(self, robots: RobotsFile) -> None:
        self.status = robots.status
        self._rules: Protego | None = None
        if 200 <= robots.status < 300:
            self._rules = Protego.parse(robots.body)
            self.mode = "rules"
        elif 400 <= robots.status < 500:
            self.mode = "allow_all"
        else:
            self.mode = "disallow_all"

    def can_fetch(self, url: str, user_agent: str) -> bool:
        if urlsplit(url).path == "/robots.txt":
            return True  # always readable (RFC 9309, section 2.2.2)
        if self._rules is not None:
            return bool(self._rules.can_fetch(url, user_agent))
        return self.mode == "allow_all"

    def crawl_delay(self, user_agent: str) -> float | None:
        if self._rules is None:
            return None
        delay = self._rules.crawl_delay(user_agent)
        return float(delay) if delay is not None else None


def robots_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def http_fetch_robots(url: str, user_agent: str, timeout: float = 10.0) -> RobotsFile:
    """Download a robots.txt. Network errors become status 0 (unreachable)."""
    try:
        with httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as client:
            response = client.get(url)
    except httpx.HTTPError:
        return RobotsFile(status=0)
    body = response.content[:MAX_BYTES].decode("utf-8", errors="replace")
    return RobotsFile(status=response.status_code, body=body)


class RobotsChecker:
    """Answers "may this worker fetch this URL?" with a Redis-shared cache."""

    def __init__(
        self,
        redis: Redis,
        user_agent: str,
        fetch: Callable[[str, str], RobotsFile] = http_fetch_robots,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.redis = redis
        self.user_agent = user_agent
        self.fetch = fetch
        self.clock = clock
        self.sleep = sleep
        self._local: dict[str, tuple[RobotsPolicy, float]] = {}

    def allowed(self, url: str) -> bool:
        return self.policy(url).can_fetch(url, self.user_agent)

    def crawl_delay(self, url: str) -> float | None:
        return self.policy(url).crawl_delay(self.user_agent)

    def policy(self, url: str) -> RobotsPolicy:
        location = robots_url(url)
        cached = self._local.get(location)
        if cached is not None and cached[1] > self.clock():
            return cached[0]
        policy = RobotsPolicy(self._load(location))
        self._local[location] = (policy, self.clock() + LOCAL_TTL)
        return policy

    def _load(self, location: str) -> RobotsFile:
        key = f"robots:{location}"
        if (stored := self._read(key)) is not None:
            return stored

        lock = f"robots:lock:{location}"
        if self.redis.set(lock, "1", nx=True, ex=LOCK_TTL):
            try:
                return self._fetch_and_store(key, location)
            finally:
                self.redis.delete(lock)

        # Another worker is fetching it: wait briefly for its result.
        deadline = self.clock() + LOCK_WAIT
        while self.clock() < deadline:
            self.sleep(0.2)
            if (stored := self._read(key)) is not None:
                return stored
        return self._fetch_and_store(key, location)

    def _read(self, key: str) -> RobotsFile | None:
        raw = self.redis.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return RobotsFile(status=data["status"], body=data["body"])

    def _fetch_and_store(self, key: str, location: str) -> RobotsFile:
        robots = self.fetch(location, self.user_agent)
        reachable = 200 <= robots.status < 500
        payload = json.dumps({"status": robots.status, "body": robots.body})
        self.redis.set(key, payload, ex=CACHE_TTL if reachable else ERROR_TTL)
        return robots
