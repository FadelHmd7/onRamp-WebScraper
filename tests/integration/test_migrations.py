"""Migrations against a real PostgreSQL.

Runs only when DATABASE_URL is set explicitly (CI sets it). The test drops and
recreates every table, so never point it at a database you care about.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from onramp_scraper.storage.db import sqlalchemy_url
from onramp_scraper.storage.migrate import alembic_config, sync_sites
from onramp_scraper.storage.models import Base

pytestmark = pytest.mark.integration

TABLES = {
    "sites",
    "users",
    "crawls",
    "crawl_urls",
    "pages",
    "fetches",
    "document_versions",
    "links",
    "dead_letters",
}


@pytest.fixture(scope="module")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("set DATABASE_URL to run against a real PostgreSQL")
    return url


@pytest.fixture(scope="module")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(sqlalchemy_url(database_url))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable")
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


def test_upgrade_creates_every_table(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= TABLES


def test_migration_matches_the_models(engine: Engine) -> None:
    """The hand-written migration and models.py must describe the same schema."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_sites_sync_is_idempotent(engine: Engine) -> None:
    assert sync_sites(engine) == sync_sites(engine) == 4
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM sites")) == 4


def _insert_version(engine: Engine, url: str, title: str, body: str) -> None:
    with engine.begin() as connection:
        page_id = connection.scalar(
            text("INSERT INTO pages (site_key, url) VALUES ('testsite', :url) RETURNING id"),
            {"url": url},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(page_id, version, content_hash, title, blocks, text_content, crawled_at) "
                "VALUES (:page, 1, :hash, :title, '[]', :body, :now)"
            ),
            {
                "page": page_id,
                "hash": "a" * 64,
                "title": title,
                "body": body,
                "now": datetime.now(UTC),
            },
        )


def test_full_text_search_uses_the_gin_index_and_highlights(engine: Engine) -> None:
    sync_sites(engine)
    _insert_version(
        engine,
        "http://testsite:8000/items/fts",
        "Pride and Prejudice",
        "A novel of manners about Elizabeth Bennet and Mr Darcy.",
    )
    with engine.connect() as connection:
        words = {"q": "darcy manners"}  # bound parameter, as the API will send it
        row = connection.execute(
            text(
                "SELECT title, ts_headline('english', text_content, query) "
                "FROM document_versions, websearch_to_tsquery('english', :q) AS query "
                "WHERE search @@ query"
            ),
            words,
        ).one()
        assert row[0] == "Pride and Prejudice"
        assert "<b>Darcy</b>" in row[1] and "<b>manners</b>" in row[1]

        connection.execute(text("SET enable_seqscan = off"))  # tiny table: force the plan
        plan = "\n".join(
            connection.scalars(
                text(
                    "EXPLAIN SELECT id FROM document_versions "
                    "WHERE search @@ websearch_to_tsquery('english', :q)"
                ),
                words,
            )
        )
        assert "ix_document_versions_search" in plan


def test_title_matches_rank_above_body_matches(engine: Engine) -> None:
    sync_sites(engine)
    _insert_version(engine, "http://testsite:8000/items/rank-a", "Whale stories", "Short.")
    _insert_version(engine, "http://testsite:8000/items/rank-b", "Sea stories", "A whale appears.")
    with engine.connect() as connection:
        titles = connection.scalars(
            text(
                "SELECT title FROM document_versions, websearch_to_tsquery('english', 'whale') q "
                "WHERE search @@ q ORDER BY ts_rank(search, q) DESC"
            )
        ).all()
    assert titles[:2] == ["Whale stories", "Sea stories"]


def test_constraints_reject_bad_rows(engine: Engine) -> None:
    sync_sites(engine)
    _insert_version(engine, "http://testsite:8000/items/dup", "Dup", "x")
    with pytest.raises(IntegrityError), engine.begin() as connection:  # same version twice
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(page_id, version, content_hash, title, blocks, crawled_at) "
                "SELECT id, 1, :hash, 'Dup', '[]', now() FROM pages WHERE url = :url"
            ),
            {"hash": "b" * 64, "url": "http://testsite:8000/items/dup"},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:  # unknown page status
        connection.execute(
            text("INSERT INTO pages (site_key, url, status) VALUES ('testsite', 'u', 'deleted')")
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:  # unknown role
        connection.execute(
            text("INSERT INTO users (username, password_hash, role) VALUES ('x', 'h', 'root')")
        )


def test_downgrade_and_upgrade_again(engine: Engine, database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES


def test_0004_puts_back_retries_stuck_in_a_cancelled_crawl(
    engine: Engine, database_url: str
) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "0003")
    sync_sites(engine)
    with engine.begin() as connection:
        ids = {}
        for name, crawl_status in (("cancelled", "failed"), ("live", "running")):
            ids[name] = connection.execute(
                text(
                    "INSERT INTO crawls (id, site_key, mode, status) "
                    "VALUES (gen_random_uuid(), 'testsite', 'full', :s) RETURNING id"
                ),
                {"s": crawl_status},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO crawl_urls (crawl_id, url, depth, status, attempts) "
                    "VALUES (:c, 'http://testsite:8000/broken/1', 0, 'queued', 0)"
                ),
                {"c": ids[name]},
            )
            connection.execute(
                text(
                    "INSERT INTO dead_letters (crawl_id, site_key, url, depth, attempts, "
                    "last_error, status, failed_at, retried_at) VALUES (:c, 'testsite', "
                    "'http://testsite:8000/broken/1', 0, 5, 'HTTP 503', 'retried', now(), now())"
                ),
                {"c": ids[name]},
            )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                text(
                    "SELECT d.crawl_id, d.status, d.retried_at IS NULL, u.status, u.attempts, "
                    "u.last_error FROM dead_letters d JOIN crawl_urls u "
                    "ON u.crawl_id = d.crawl_id AND u.url = d.url"
                )
            )
        }
    assert rows[ids["cancelled"]] == ("dead", True, "dead", 5, "HTTP 503")
    assert rows[ids["live"]] == ("retried", False, "queued", 0, None)  # still on its way
