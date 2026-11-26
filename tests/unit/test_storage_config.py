import pytest

from onramp_scraper.storage.db import sqlalchemy_url
from onramp_scraper.storage.migrate import MIGRATIONS
from onramp_scraper.storage.models import Base


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@postgres:5432/db", "postgresql+psycopg://u:p@postgres:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_sqlalchemy_url(given: str, expected: str) -> None:
    assert sqlalchemy_url(given) == expected


def test_rejects_other_databases() -> None:
    with pytest.raises(ValueError):
        sqlalchemy_url("mysql://u:p@h/db")


def test_every_table_has_a_natural_key() -> None:
    """Idempotent writes need a unique key on every table (see DATA_MODEL.md)."""
    for table in Base.metadata.sorted_tables:
        unique = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert table.primary_key.columns or unique, table.name


def test_migrations_ship_inside_the_package() -> None:
    assert (MIGRATIONS / "env.py").is_file()
    assert list((MIGRATIONS / "versions").glob("0001_*.py"))
