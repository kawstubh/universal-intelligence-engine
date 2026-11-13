"""Add idempotent, privacy-minimised public appointment request references."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0013_public_booking_idempotency"
down_revision = "0012_clinic_assistant_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_public_booking_requests"):
        op.create_table(
            "dental_public_booking_requests",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("phone_hash", sa.String(64), nullable=False),
            sa.Column("starts_at_utc", sa.Text(), nullable=False),
            sa.Column("public_reference", sa.String(32), nullable=False),
            sa.Column("patient_id", sa.String(64), sa.ForeignKey("dental_patients.id"), nullable=True),
            sa.Column("appointment_id", sa.String(64), sa.ForeignKey("dental_appointments.id"), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("clinic_id", "idempotency_key_hash", name="uq_dental_public_booking_idempotency"),
            sa.UniqueConstraint("clinic_id", "phone_hash", "starts_at_utc", name="uq_dental_public_booking_phone_time"),
            sa.UniqueConstraint("clinic_id", "public_reference", name="uq_dental_public_booking_reference"),
        )
    inspector = inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("dental_public_booking_requests")}
    if "idx_dental_public_booking_clinic_created" not in indexes:
        op.create_index(
            "idx_dental_public_booking_clinic_created",
            "dental_public_booking_requests",
            ["clinic_id", "created_at"],
        )


def downgrade() -> None:
    # Forward-only: retain booking references and idempotency history.
    pass
