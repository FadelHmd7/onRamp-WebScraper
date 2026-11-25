"""Database tables. The design and its reasons are in docs/phase1/DATA_MODEL.md.

Every table has a natural unique key, so workers can write with
``INSERT ... ON CONFLICT`` and a job that runs twice changes nothing.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names, so migrations can refer to them.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

CRAWL_MODES = ("full", "incremental")
CRAWL_STATUSES = ("queued", "running", "finished", "failed")
URL_STATUSES = ("queued", "fetched", "unchanged", "skipped", "failed", "dead")
PAGE_STATUSES = ("active", "removed")
DEAD_LETTER_STATUSES = ("dead", "retried", "resolved")
ROLES = ("admin", "viewer")

SEARCH_VECTOR = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(text_content, '')), 'B')"
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Site(Base):
    __tablename__ = "sites"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_one_of("role", ROLES), name="role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _now()


class Crawl(Base):
    __tablename__ = "crawls"
    __table_args__ = (
        CheckConstraint(_one_of("mode", CRAWL_MODES), name="mode"),
        CheckConstraint(_one_of("status", CRAWL_STATUSES), name="status"),
        CheckConstraint("max_depth >= 0", name="max_depth"),
        Index("ix_crawls_site_key_created_at", "site_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_key: Mapped[str] = mapped_column(ForeignKey("sites.key"))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL: the site's configured seeds. A list: custom seeds (a demo, a retry);
    # such a crawl never marks unreached pages as removed (migration 0002).
    seeds: Mapped[list[str] | None] = mapped_column(JSONB)
    # NULL: the site's max_depth. Lower values crawl less deep (migration 0003).
    max_depth: Mapped[int | None] = mapped_column(Integer)


class CrawlUrl(Base):
    """One URL's state inside one crawl. Progress = counts of these by status."""

    __tablename__ = "crawl_urls"
    __table_args__ = (
        CheckConstraint(_one_of("status", URL_STATUSES), name="status"),
        CheckConstraint("depth >= 0", name="depth"),
        Index("ix_crawl_urls_crawl_id_status", "crawl_id", "status"),
    )

    crawl_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawls.id", ondelete="CASCADE"), primary_key=True
    )
    url: Mapped[str] = mapped_column(Text, primary_key=True)
    depth: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        CheckConstraint(_one_of("status", PAGE_STATUSES), name="status"),
        Index("ix_pages_site_key_status", "site_key", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_key: Mapped[str] = mapped_column(ForeignKey("sites.key"))
    url: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    current_version: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fetch(Base):
    """Raw data: the HTML, status code and time of one page's fetch in one crawl."""

    __tablename__ = "fetches"
    __table_args__ = (UniqueConstraint("crawl_id", "page_id", name="uq_fetches_crawl_id_page_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crawl_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawls.id", ondelete="CASCADE"))
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status_code: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    html: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, server_default="{}")
    elapsed_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class DocumentVersion(Base):
    """One cleaned, validated version of a page. Never updated after insert."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("page_id", "version", name="uq_document_versions_page_id_version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("char_length(content_hash) = 64", name="content_hash"),
        Index("ix_document_versions_search", "search", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    text_content: Mapped[str] = mapped_column(Text, server_default="")
    search: Mapped[Any] = mapped_column(TSVECTOR, Computed(SEARCH_VECTOR, persisted=True))
    crawl_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawls.id", ondelete="SET NULL"))
    fetch_id: Mapped[int | None] = mapped_column(ForeignKey("fetches.id", ondelete="SET NULL"))
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Link(Base):
    """A link on one version of a page: source (the version), target URL, link text."""

    __tablename__ = "links"
    __table_args__ = (Index("ix_links_target_url", "target_url"),)

    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_url: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, server_default="")
    nofollow: Mapped[bool] = mapped_column(Boolean, server_default="false")


class DeadLetter(Base):
    """A URL that failed every attempt in a crawl. Admins can retry it."""

    __tablename__ = "dead_letters"
    __table_args__ = (
        UniqueConstraint("crawl_id", "url", name="uq_dead_letters_crawl_id_url"),
        CheckConstraint(_one_of("status", DEAD_LETTER_STATUSES), name="status"),
        Index("ix_dead_letters_status_failed_at", "status", "failed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crawl_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawls.id", ondelete="CASCADE"))
    site_key: Mapped[str] = mapped_column(ForeignKey("sites.key"))
    url: Mapped[str] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    last_error: Mapped[str] = mapped_column(Text)
    last_status: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="dead")
    failed_at: Mapped[datetime] = _now()
    retried_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
