"""The shared resources of one worker process, built on first use.

Celery forks its worker process after importing the app, so nothing here is
created at import time: connections opened before a fork would be shared by
two processes.
"""

from collections.abc import Callable
from urllib.parse import urlsplit

from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from onramp_scraper.config import Settings
from onramp_scraper.crawler.fetch import HttpFetcher, PoliteFetcher, rate_limited_robots_fetch
from onramp_scraper.crawler.frontier import Frontier
from onramp_scraper.crawler.ratelimit import RateLimiter
from onramp_scraper.crawler.robots import RobotsChecker
from onramp_scraper.sites import SiteConfig
from onramp_scraper.worker.pipeline import CrawlContext, Enqueue
from onramp_scraper.worker.retry import RetryPolicy


def site_rate(sites: dict[str, SiteConfig], default: float) -> Callable[[str], float]:
    """requests_per_second for a URL's host, from the site that owns it."""
    by_host = {host: s.requests_per_second for s in sites.values() for host in s.allowed_hosts}

    def rate(url: str) -> float:
        return by_host.get(urlsplit(url).hostname or "", default)

    return rate


def build_context(
    settings: Settings,
    sites: dict[str, SiteConfig],
    engine: Engine,
    redis: Redis,
    enqueue: Enqueue,
) -> CrawlContext:
    limiter = RateLimiter(redis)
    robots = RobotsChecker(
        redis,
        settings.user_agent,
        fetch=rate_limited_robots_fetch(limiter, site_rate(sites, settings.requests_per_second)),
    )
    fetcher = PoliteFetcher(HttpFetcher(settings.user_agent), robots, limiter)
    return CrawlContext(
        sessions=sessionmaker(bind=engine, expire_on_commit=False),
        fetcher=fetcher,
        frontier=Frontier(redis),
        sites=sites,
        enqueue=enqueue,
        retry=RetryPolicy(
            max_attempts=settings.max_fetch_attempts,
            base_delay=settings.retry_base_delay,
            max_delay=settings.retry_max_delay,
        ),
    )
