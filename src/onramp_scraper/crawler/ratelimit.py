"""Per-domain rate limit shared by every worker, stored in Redis.

The spec: with 8 workers and a limit of 1 request per second, the site must
still get at most 1 request per second in total. A sleep inside each worker
cannot do that, because each worker would only space out its own requests.

How it works (a token bucket in its "virtual scheduling" form, also called
GCRA, the Generic Cell Rate Algorithm):

- Redis keeps one number per host: the next free time slot.
- Before a request, a worker runs one Lua script. The script reads the clock
  from Redis (so all containers share one clock), works out how long this
  worker must wait, and moves the next free slot forward by one interval.
- Redis runs a script atomically, so two workers can never take the same slot.
  With 8 workers at 1 request/second they get slots at +0s, +1s, ... +7s.
- ``burst`` is the bucket size: how many requests may go back-to-back after
  an idle period. The default of 1 means strictly evenly spaced requests.

A worker learns its exact wait in one round trip and sleeps once; it never
polls. A reserved slot that goes unused (the worker crashed) only makes the
crawl slightly slower, never faster than the limit.
"""

import math
import time
from collections.abc import Callable

from redis import Redis

_RESERVE_SLOT = """
local key = KEYS[1]
local interval = tonumber(ARGV[1])        -- microseconds between requests
local burst = tonumber(ARGV[2])           -- bucket size
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000000 + tonumber(t[2])

local next_free = tonumber(redis.call('GET', key) or now)
if next_free < now then next_free = now end

local wait = next_free - (burst - 1) * interval - now
if wait < 0 then wait = 0 end

local new_next_free = next_free + interval
-- Expire the key once it no longer matters, so idle hosts leave nothing behind.
local ttl_ms = math.ceil((new_next_free - now) / 1000) + 1000
-- '%.0f', not '%d': '%d' goes through a C long, which is 32 bits on some
-- platforms and would overflow. Doubles hold integers exactly up to 2^53.
redis.call('SET', key, string.format('%.0f', new_next_free), 'PX', ttl_ms)
return wait
"""


def effective_rate(requests_per_second: float, crawl_delay: float | None) -> float:
    """Our configured rate, lowered further if the site's Crawl-delay asks for it."""
    if crawl_delay and crawl_delay > 0:
        return min(requests_per_second, 1.0 / crawl_delay)
    return requests_per_second


class RateLimiter:
    def __init__(self, redis: Redis, sleep: Callable[[float], None] = time.sleep) -> None:
        self._reserve_slot = redis.register_script(_RESERVE_SLOT)
        self._sleep = sleep

    def reserve(self, host: str, requests_per_second: float, burst: int = 1) -> float:
        """Take the next slot for ``host``. Returns the seconds to wait before using it."""
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        interval_us = math.ceil(1_000_000 / requests_per_second)
        wait_us = int(self._reserve_slot(keys=[f"ratelimit:{host}"], args=[interval_us, burst]))
        return wait_us / 1_000_000

    def wait(self, host: str, requests_per_second: float, burst: int = 1) -> float:
        """Block until this worker may send its next request to ``host``."""
        delay = self.reserve(host, requests_per_second, burst)
        if delay > 0:
            self._sleep(delay)
        return delay
