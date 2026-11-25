"""Apply database migrations: ``python -m onramp_scraper.storage.migrate``.

The ``migrate`` Compose service runs this once on every ``docker compose up``;
the API and workers start only after it succeeds. Alembic is configured here
in code, so no ``alembic.ini`` has to ship with the image.

    python -m onramp_scraper.storage.migrate              # upgrade to the latest
    python -m onramp_scraper.storage.migrate downgrade base
"""

import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError

from onramp_scraper.config import get_settings
from onramp_scraper.sites import load_sites
from onramp_scraper.storage.db import sqlalchemy_url
from onramp_scraper.storage.models import Site

MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option(
        "sqlalchemy.url", sqlalchemy_url(database_url or get_settings().database_url)
    )
    return config


def wait_for_database(url: str, attempts: int = 30) -> None:
    """Postgres may still be starting; retry for up to ~30 seconds."""
    engine = create_engine(url)
    try:
        for attempt in range(1, attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except OperationalError:
                if attempt == attempts:
                    raise
                time.sleep(1)
    finally:
        engine.dispose()


def sync_sites(engine: Engine) -> int:
    """Upsert every site from config/sites.yaml, so crawls can reference it."""
    rows = [
        {"key": site.key, "name": site.name, "base_url": str(site.base_url)}
        for site in load_sites().values()
    ]
    statement = insert(Site).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Site.key],
        set_={"name": statement.excluded.name, "base_url": statement.excluded.base_url},
    )
    with engine.begin() as connection:
        connection.execute(statement)
    return len(rows)


def main(argv: list[str]) -> int:
    config = alembic_config()
    url = config.get_main_option("sqlalchemy.url") or ""
    wait_for_database(url)
    if argv[:1] == ["downgrade"]:
        command.downgrade(config, argv[1] if len(argv) > 1 else "-1")
        return 0
    command.upgrade(config, "head")
    command.current(config)
    engine = create_engine(url)
    try:
        print(f"synced {sync_sites(engine)} sites from {get_settings().sites_file}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
