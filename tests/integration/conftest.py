"""A real PostgreSQL for storage tests.

Runs only when DATABASE_URL is set explicitly (CI sets it). Tables are
emptied before each test, so never point it at a database you care about.
"""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage.db import sqlalchemy_url
from onramp_scraper.storage.migrate import alembic_config, sync_sites

# Everything that crawls write; sites and users are kept.
CRAWL_TABLES = "links, document_versions, fetches, crawl_urls, dead_letters, pages, crawls"


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("set DATABASE_URL to run against a real PostgreSQL")
    engine = create_engine(sqlalchemy_url(url))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable")
    command.upgrade(alembic_config(url), "head")
    sync_sites(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(pg_engine: Engine) -> sessionmaker[Session]:
    """A session factory on freshly emptied crawl tables."""
    with pg_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {CRAWL_TABLES} RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=pg_engine, expire_on_commit=False)
