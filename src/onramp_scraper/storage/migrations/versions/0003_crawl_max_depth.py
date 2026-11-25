"""Let one crawl go less deep than its site allows.

Demos and re-checks crawl a few chosen URLs without following their links
into the whole site (``max_depth = 0``). NULL means the site's own limit.
A depth-limited crawl never marks unreached pages as removed.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crawls", sa.Column("max_depth", sa.Integer(), nullable=True))
    op.create_check_constraint("max_depth", "crawls", "max_depth >= 0")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_crawls_max_depth"), "crawls", type_="check")  # name is final
    op.drop_column("crawls", "max_depth")
