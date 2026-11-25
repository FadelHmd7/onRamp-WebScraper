"""A real PostgreSQL for storage tests.

Runs only when DATABASE_URL is set explicitly (CI sets it). Tables are
emptied before each test, so never point it at a database you care about.
"""

import os
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from alembic import command
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage.db import sqlalchemy_url
from onramp_scraper.storage.migrate import alembic_config, sync_sites
from onramp_testsite import app as testsite

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


@pytest.fixture(scope="module")
def live_testsite() -> Iterator[str]:
    """The test site served by uvicorn on a free local port, without added latency."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    testsite.LATENCY_SECONDS = 0
    testsite.state.reset()
    server = uvicorn.Server(
        uvicorn.Config(testsite.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/health", timeout=0.5)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def redis_client() -> Iterator[Redis]:
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("set REDIS_URL to run against a real Redis")
    client = Redis.from_url(url)
    try:
        client.ping()
    except RedisError:
        pytest.skip("Redis is not reachable")
    yield client
    client.close()
