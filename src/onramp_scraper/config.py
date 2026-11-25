"""Settings for every component, read from environment variables.

Each container reads the same variables. Defaults suit `docker compose up`
from a clean checkout; `.env.example` lists every variable.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "info"

    # Sent with every request. Must name a real contact before crawling real sites.
    user_agent: str = "onRamp-WebScraper/0.1 (contact: you@example.com)"

    database_url: str = "postgresql://scraper:scraper@postgres:5432/scraper"
    redis_url: str = "redis://redis:6379/0"
    broker_url: str = "amqp://scraper:scraper@rabbitmq:5672//"

    # Per-site crawl rules (seeds, limits, fetch mode, selectors).
    sites_file: Path = Path("config/sites.yaml")

    # Crawl limits. The per-domain rate is shared by every worker.
    default_max_depth: int = Field(default=2, ge=0)
    default_max_pages: int = Field(default=50, ge=1)
    requests_per_second: float = Field(default=1.0, gt=0)
    max_fetch_attempts: int = Field(default=5, ge=1)
    # Retry backoff: attempt n waits up to base * 2**(n-1) seconds, at most max.
    retry_base_delay: float = Field(default=2.0, ge=0)
    retry_max_delay: float = Field(default=300.0, gt=0)
    # A job running longer than this is stopped and counts as a failed attempt.
    task_soft_time_limit: int = Field(default=120, ge=10)
    # A worker refreshes its heartbeat key every interval; a dead one's key
    # expires after the TTL, so "active workers" drops within the TTL.
    heartbeat_ttl: float = Field(default=15.0, gt=0)
    heartbeat_interval: float = Field(default=5.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
