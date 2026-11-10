"""Add workspace context to new Dental audit events."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0007_dental_audit_workspace"
down_revision = "0006_multi_tenant_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("dental_audit_events")
    }
    if "clinic_id" not in columns:
        op.add_column(
            "dental_audit_events",
            sa.Column("clinic_id", sa.String(128), nullable=True),
        )
    op.create_index(
        "idx_dental_audit_events_clinic_created",
        "dental_audit_events",
        ["clinic_id", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    # Retain audit workspace context so audit history is not destroyed.
    pass
