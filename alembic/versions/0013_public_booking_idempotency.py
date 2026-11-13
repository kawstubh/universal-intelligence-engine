"""Persist public booking idempotency receipts without exposing patient IDs."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0013_public_booking_idempotency"
down_revision = "0012_clinic_assistant_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_public_booking_requests"):
        op.create_table(
            "dental_public_booking_requests",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), nullable=False),
            sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("duplicate_hash", sa.String(64), nullable=False),
            sa.Column("public_reference", sa.String(32), nullable=False),
            sa.Column(
                "patient_id",
                sa.String(64),
                sa.ForeignKey("dental_patients.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "appointment_id",
                sa.String(64),
                sa.ForeignKey("dental_appointments.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint(
                "clinic_id",
                "idempotency_key_hash",
                name="uq_dental_public_booking_idempotency",
            ),
            sa.UniqueConstraint(
                "public_reference",
                name="uq_dental_public_booking_reference",
            ),
        )
    op.create_index(
        "idx_dental_public_booking_duplicate_window",
        "dental_public_booking_requests",
        ["clinic_id", "duplicate_hash", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("dental_public_booking_requests"):
        op.drop_table("dental_public_booking_requests")
