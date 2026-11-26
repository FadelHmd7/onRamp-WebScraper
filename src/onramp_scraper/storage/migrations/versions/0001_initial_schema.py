"""Initial schema: sites, users, crawls, crawl URLs, pages, raw fetches,
document versions with a full-text index, links and the dead-letter queue.

Design notes: docs/phase1/DATA_MODEL.md.

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(text_content, '')), 'B')"
)


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("key", name="pk_sites"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        _created_at(),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "crawls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_key", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        _created_at(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('full', 'incremental')", name="ck_crawls_mode"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed')", name="ck_crawls_status"
        ),
        sa.ForeignKeyConstraint(["site_key"], ["sites.key"], name="fk_crawls_site_key_sites"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_crawls_created_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_crawls"),
    )
    op.create_index("ix_crawls_site_key_created_at", "crawls", ["site_key", "created_at"])

    op.create_table(
        "crawl_urls",
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'fetched', 'unchanged', 'skipped', 'failed', 'dead')",
            name="ck_crawl_urls_status",
        ),
        sa.CheckConstraint("depth >= 0", name="ck_crawl_urls_depth"),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["crawls.id"], name="fk_crawl_urls_crawl_id_crawls", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("crawl_id", "url", name="pk_crawl_urls"),
    )
    op.create_index("ix_crawl_urls_crawl_id_status", "crawl_urls", ["crawl_id", "status"])

    op.create_table(
        "pages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("site_key", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'removed')", name="ck_pages_status"),
        sa.ForeignKeyConstraint(["site_key"], ["sites.key"], name="fk_pages_site_key_sites"),
        sa.PrimaryKeyConstraint("id", name="pk_pages"),
        sa.UniqueConstraint("url", name="uq_pages_url"),
    )
    op.create_index("ix_pages_site_key_status", "pages", ["site_key", "status"])

    op.create_table(
        "fetches",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", sa.BigInteger(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column(
            "headers", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("elapsed_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["crawls.id"], name="fk_fetches_crawl_id_crawls", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], name="fk_fetches_page_id_pages"),
        sa.PrimaryKeyConstraint("id", name="pk_fetches"),
        sa.UniqueConstraint("crawl_id", "page_id", name="uq_fetches_crawl_id_page_id"),
    )
    op.create_index("ix_fetches_page_id", "fetches", ["page_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("page_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "fields", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("text_content", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR, persisted=True),
            nullable=True,
        ),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fetch_id", sa.BigInteger(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_document_versions_version"),
        sa.CheckConstraint(
            "char_length(content_hash) = 64", name="ck_document_versions_content_hash"
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["pages.id"], name="fk_document_versions_page_id_pages"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"],
            ["crawls.id"],
            name="fk_document_versions_crawl_id_crawls",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["fetch_id"],
            ["fetches.id"],
            name="fk_document_versions_fetch_id_fetches",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("page_id", "version", name="uq_document_versions_page_id_version"),
    )
    op.create_index(
        "ix_document_versions_search", "document_versions", ["search"], postgresql_using="gin"
    )

    op.create_table(
        "links",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("nofollow", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
            name="fk_links_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "position", name="pk_links"),
    )
    op.create_index("ix_links_target_url", "links", ["target_url"])

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_key", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), server_default="dead", nullable=False),
        sa.Column(
            "failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("retried_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('dead', 'retried', 'resolved')", name="ck_dead_letters_status"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["crawls.id"], name="fk_dead_letters_crawl_id_crawls", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["site_key"], ["sites.key"], name="fk_dead_letters_site_key_sites"),
        sa.PrimaryKeyConstraint("id", name="pk_dead_letters"),
        sa.UniqueConstraint("crawl_id", "url", name="uq_dead_letters_crawl_id_url"),
    )
    op.create_index("ix_dead_letters_status_failed_at", "dead_letters", ["status", "failed_at"])


def downgrade() -> None:
    for table in (
        "dead_letters",
        "links",
        "document_versions",
        "fetches",
        "pages",
        "crawl_urls",
        "crawls",
        "users",
        "sites",
    ):
        op.drop_table(table)
