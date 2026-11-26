"""The autoscaler: scale the worker service from the crawl queue's backlog.

    docker compose --profile autoscale up -d

Every AUTOSCALE_INTERVAL seconds: read the queue depth from RabbitMQ, decide
with ``policy.Scaler``, and add or remove worker containers through the
Docker socket. See policy.py for the rules (up fast, down slowly).
"""

import logging
import socket
import time

import httpx

from onramp_scraper.autoscaler.docker import PROJECT_LABEL, DockerWorkers
from onramp_scraper.autoscaler.policy import ScalePolicy, Scaler
from onramp_scraper.config import get_settings
from onramp_scraper.queue_stats import queue_depth

log = logging.getLogger("onramp_scraper.autoscaler")


def own_project(fallback: str) -> str:
    """The Compose project this container belongs to (from its own labels)."""
    probe = DockerWorkers(fallback)
    try:
        spec = probe._call("GET", f"/containers/{socket.gethostname()}/json")
        return str(spec["Config"]["Labels"].get(PROJECT_LABEL, fallback))
    except httpx.HTTPError:
        return fallback


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # not every poll, only decisions
    policy = ScalePolicy(
        min_workers=settings.autoscale_min,
        max_workers=settings.autoscale_max,
        jobs_per_worker=settings.autoscale_jobs_per_worker,
        cooldown=settings.autoscale_cooldown,
    )
    scaler = Scaler(policy)
    workers = DockerWorkers(own_project(settings.compose_project))
    log.info("autoscaling project %s with %s", workers.project, policy)

    last = None
    while True:
        try:
            depth = queue_depth(settings)
            current = len(workers.running())
            target = scaler.target(current, depth.backlog, time.monotonic())
            state = (depth.backlog, current, target)
            if state != last:  # log changes only, not every tick
                log.info(
                    "backlog %d (ready %d, unacked %d), workers %d -> %d",
                    depth.backlog, depth.ready, depth.unacked, current, target,
                )  # fmt: skip
                last = state
            if target != current:
                added, removed = workers.scale_to(target)
                for worker in added:
                    log.info("started %s", worker.name)
                for worker in removed:
                    log.info("stopped %s", worker.name)
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("cannot scale right now: %s", exc)
        time.sleep(settings.autoscale_interval)


if __name__ == "__main__":
    main()
