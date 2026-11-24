import pytest
from pydantic import ValidationError

from onramp_scraper.config import Settings


def test_defaults_point_at_compose_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    assert "@postgres:5432" in settings.database_url
    assert settings.redis_url == "redis://redis:6379/0"
    assert "@rabbitmq:5672" in settings.broker_url


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUESTS_PER_SECOND", "2.5")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    settings = Settings(_env_file=None)
    assert settings.requests_per_second == 2.5
    assert settings.redis_url == "redis://localhost:6379/1"


def test_rejects_invalid_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUESTS_PER_SECOND", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
