"""Settings for every component, read from environment variables.

Each container reads the same variables. Defaults suit `docker compose up`
from a clean checkout; `.env.example` lists every variable.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local-development defaults, so `docker compose up` works from a clean clone.
# Settings refuses all three unless APP_ENV is "development" (see below).
DEV_JWT_SECRET = "dev-only-jwt-secret-not-for-production-use"  # noqa: S105
DEV_ADMIN_PASSWORD = "admin-dev-password"  # noqa: S105
DEV_VIEWER_PASSWORD = "viewer-dev-password"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # anything else refuses the default secrets below
    log_level: str = "info"

    # Sent with every request. Must name a real contact before crawling real sites.
    user_agent: str = "onRamp-WebScraper/0.1 (contact: you@example.com)"

    database_url: str = "postgresql://scraper:scraper@postgres:5432/scraper"
    redis_url: str = "redis://redis:6379/0"
    broker_url: str = "amqp://scraper:scraper@rabbitmq:5672//"
    # RabbitMQ's management API, for queue depth (same credentials as BROKER_URL).
    rabbitmq_api_url: str = "http://rabbitmq:15672"

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

    # API authentication. The defaults work for local development only: with any
    # APP_ENV other than "development", the API refuses to start on them.
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_ttl_minutes: int = Field(default=60, ge=1, le=24 * 60)
    # Seeded by the migrate service on every start (storage/users.py).
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr(DEV_ADMIN_PASSWORD)
    viewer_username: str = "viewer"
    viewer_password: SecretStr = SecretStr(DEV_VIEWER_PASSWORD)

    # Autoscaler (bonus, `docker compose --profile autoscale up`): see autoscaler/policy.py.
    compose_project: str = "onramp-scraper"  # used only if it cannot read its own labels
    autoscale_min: int = Field(default=1, ge=1)
    autoscale_max: int = Field(default=8, ge=1)
    autoscale_jobs_per_worker: int = Field(default=25, ge=1)
    autoscale_cooldown: float = Field(default=30.0, ge=0)
    autoscale_interval: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def no_dev_secrets_outside_development(self) -> "Settings":
        if self.app_env == "development":
            return self
        defaults = {
            "JWT_SECRET": self.jwt_secret.get_secret_value() == DEV_JWT_SECRET,
            "ADMIN_PASSWORD": self.admin_password.get_secret_value() == DEV_ADMIN_PASSWORD,
            "VIEWER_PASSWORD": self.viewer_password.get_secret_value() == DEV_VIEWER_PASSWORD,
        }
        if unsafe := [name for name, is_default in defaults.items() if is_default]:
            raise ValueError(f"APP_ENV={self.app_env!r} needs real values for {unsafe}")
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
