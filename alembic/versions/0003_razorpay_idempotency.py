from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_razorpay_idempotency"
down_revision = "0002_dental_ai_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table("dental_razorpay_events"):
        op.create_table(
            "dental_razorpay_events",
            sa.Column("event_id", sa.String(128), primary_key=True),
            sa.Column("event_name", sa.String(100), nullable=False),
            sa.Column("processed_at", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("dental_razorpay_events")
