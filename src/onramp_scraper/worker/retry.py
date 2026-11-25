"""When to try a failed fetch again.

Exponential backoff with full jitter: attempt n waits a random time between
0 and ``min(cap, base * 2**(n-1))``. The randomness spreads retries out, so
many URLs that failed together (a site hiccup) do not all come back at the
same instant and knock the site over again.

A ``Retry-After`` from the server is a floor: we never come back sooner than
asked. (The rate limiter also pauses the whole host for that long, so other
workers wait too; see crawler/ratelimit.py.)
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 2.0  # seconds before attempt 2 (at most)
    max_delay: float = 300.0

    def should_retry(self, attempt: int) -> bool:
        """``attempt`` is the one that just failed (1 = the first)."""
        return attempt < self.max_attempts

    def delay(
        self,
        attempt: int,
        retry_after: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        """Seconds to wait before attempt ``attempt + 1``."""
        ceiling = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        jittered = (rng or random).uniform(0, ceiling)
        if retry_after is not None:
            return max(jittered, min(retry_after, self.max_delay))
        return jittered
