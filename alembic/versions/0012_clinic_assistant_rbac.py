"""Add a separate, least-privilege Clinic Assistant identity and sessions.

Assistants are not doctors and are not doctor memberships. Their credentials,
sessions, and permissions are stored separately. Existing receipt history is
preserved while adding an assistant actor column.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0012_clinic_assistant_rbac"
down_revision = "0011_document_clinical_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("dental_clinic_assistants"):
        op.create_table(
            "dental_clinic_assistants",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_doctor_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("email", name="uq_dental_clinic_assistants_email"),
        )

    if not inspector.has_table("dental_assistant_sessions"):
        op.create_table(
            "dental_assistant_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("assistant_id", sa.String(64), sa.ForeignKey("dental_clinic_assistants.id"), nullable=False),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Text(), nullable=False),
        )

    inspector = inspect(bind)
    if inspector.has_table("dental_clinic_assistants"):
        indexes = {item["name"] for item in inspector.get_indexes("dental_clinic_assistants")}
        if "idx_dental_clinic_assistants_clinic_active" not in indexes:
            op.create_index(
                "idx_dental_clinic_assistants_clinic_active",
                "dental_clinic_assistants",
                ["clinic_id", "active"],
            )

    if inspector.has_table("dental_assistant_sessions"):
        indexes = {item["name"] for item in inspector.get_indexes("dental_assistant_sessions")}
        if "idx_dental_assistant_sessions_assistant" not in indexes:
            op.create_index(
                "idx_dental_assistant_sessions_assistant",
                "dental_assistant_sessions",
                ["assistant_id", "expires_at"],
            )
        if "idx_dental_assistant_sessions_clinic" not in indexes:
            op.create_index(
                "idx_dental_assistant_sessions_clinic",
                "dental_assistant_sessions",
                ["clinic_id", "expires_at"],
            )

    if not inspector.has_table("dental_receipts"):
        raise RuntimeError("dental_receipts must exist before adding assistant receipt attribution")
    receipt_columns = {item["name"]: item for item in inspector.get_columns("dental_receipts")}
    if "created_by_assistant_id" not in receipt_columns or receipt_columns["created_by_doctor_id"]["nullable"] is False:
        with op.batch_alter_table("dental_receipts") as batch:
            if "created_by_assistant_id" not in receipt_columns:
                batch.add_column(sa.Column("created_by_assistant_id", sa.String(64), nullable=True))
            if receipt_columns["created_by_doctor_id"]["nullable"] is False:
                batch.alter_column(
                    "created_by_doctor_id",
                    existing_type=sa.String(64),
                    nullable=True,
                )


def downgrade() -> None:
    # Forward-only: preserve assistant access history and financial attribution.
    pass
