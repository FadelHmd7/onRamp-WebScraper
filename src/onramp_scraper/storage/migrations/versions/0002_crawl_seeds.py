"""Record a crawl's custom seeds.

A finished full crawl marks the site's pages it never reached as removed.
That is only true when it started from the site's configured seeds; a crawl
of a few chosen URLs (a demo, a retry) reaches little on purpose. ``seeds``
is NULL for a normal crawl and the list of URLs for a custom one.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crawls", sa.Column("seeds", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("crawls", "seeds")
