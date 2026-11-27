"""Put back dead-letter retries that can never run.

A retry of a job whose crawl had been cancelled (status 'failed') was sent,
but workers drop a cancelled crawl's jobs, so the row said "retried" and the
URL "queued" forever. The code now refuses such retries; this repairs rows
already stuck: they wait in the dead-letter queue again, as they were.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE crawl_urls AS u
           SET status = 'dead', attempts = d.attempts, last_error = d.last_error,
               updated_at = now()
          FROM dead_letters AS d
          JOIN crawls AS c ON c.id = d.crawl_id
         WHERE d.status = 'retried' AND c.status = 'failed'
           AND u.crawl_id = d.crawl_id AND u.url = d.url AND u.status = 'queued'
        """
    )
    op.execute(
        """
        UPDATE dead_letters AS d
           SET status = 'dead', retried_at = NULL
          FROM crawls AS c
         WHERE c.id = d.crawl_id AND d.status = 'retried' AND c.status = 'failed'
        """
    )


def downgrade() -> None:
    pass  # a data repair: the stuck state is not worth restoring
