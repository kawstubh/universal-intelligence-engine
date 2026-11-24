"""Create shared, privacy-preserving Oralyn rate-limit counters."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_shared_rate_limit_buckets"
down_revision = "0015_document_cleanup_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dental_rate_limit_buckets",
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("attempt_count > 0", name="ck_dental_rate_limit_attempts_positive"),
    )
    op.create_index(
        "idx_dental_rate_limit_window_start",
        "dental_rate_limit_buckets",
        ["window_start"],
    )


def downgrade() -> None:
    # Forward-only security state: do not silently remove counters on rollback.
    pass
