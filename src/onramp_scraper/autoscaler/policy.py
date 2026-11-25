"""How many workers the queue needs. Pure logic, no Docker, no RabbitMQ.

- **Up fast.** As soon as the backlog needs more workers, scale to that
  number at once: a growing queue is waiting time for every page in it.
- **Down slowly.** Remove one worker at a time, and only after the backlog
  has stayed low for ``cooldown`` seconds. Crawls come in bursts (a page
  adds 20 links at once), so the queue dips between them; scaling down on
  every dip would stop and start containers for nothing.
- Always between ``min_workers`` and ``max_workers``.

"Backlog" is ready plus unacknowledged messages: jobs waiting and jobs in
hand. A delayed retry is held unacknowledged by a worker until it is due, so
it counts too.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScalePolicy:
    min_workers: int = 1
    max_workers: int = 8
    jobs_per_worker: int = 25  # backlog one worker is expected to keep up with
    cooldown: float = 30.0  # seconds of low backlog before removing a worker

    def __post_init__(self) -> None:
        if not 0 <= self.min_workers <= self.max_workers:
            raise ValueError("need 0 <= min_workers <= max_workers")
        if self.jobs_per_worker < 1:
            raise ValueError("jobs_per_worker must be at least 1")

    def needed(self, backlog: int) -> int:
        """Workers for this backlog, within the limits."""
        wanted = math.ceil(backlog / self.jobs_per_worker)
        return max(self.min_workers, min(self.max_workers, wanted))


@dataclass
class Scaler:
    """Remembers since when the backlog has been low, to apply the cooldown."""

    policy: ScalePolicy
    low_since: float | None = None

    def target(self, current: int, backlog: int, now: float) -> int:
        """The number of workers to run now, given ``current`` and the backlog."""
        policy = self.policy
        if current > policy.max_workers:  # over the limit (scaled by hand): fix it at once
            self.low_since = None
            return policy.max_workers
        needed = policy.needed(backlog)  # never below min_workers
        if needed >= current:  # more needed, or just right: no waiting
            self.low_since = None
            return needed
        # Fewer would do: wait until the backlog has been low for a cooldown,
        # then remove one worker; the next one waits a full cooldown again.
        if self.low_since is None:
            self.low_since = now
            return current
        if now - self.low_since < policy.cooldown:
            return current
        self.low_since = now
        return current - 1
