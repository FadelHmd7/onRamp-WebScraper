"""Database connection: one engine per process, sessions on demand."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.config import get_settings


def sqlalchemy_url(database_url: str) -> str:
    """``postgresql://`` from the environment, with the psycopg 3 driver named for SQLAlchemy."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    raise ValueError("DATABASE_URL must start with postgresql://")


@lru_cache
def get_engine() -> Engine:
    # pool_pre_ping: a worker that sat idle still gets a live connection.
    return create_engine(sqlalchemy_url(get_settings().database_url), pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: committed if the block succeeds, rolled back if it raises."""
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
