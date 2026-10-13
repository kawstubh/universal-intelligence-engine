"""Add active status to clinic doctor accounts for password authentication."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0005_password_auth"
down_revision = "0004_patient_invites_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_doctors"):
        raise RuntimeError("dental_doctors must exist before password auth migration")
    columns = {column["name"] for column in inspector.get_columns("dental_doctors")}
    if "active" not in columns:
        op.add_column(
            "dental_doctors",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    # Intentionally retain the active column so account state is never lost.
    pass
