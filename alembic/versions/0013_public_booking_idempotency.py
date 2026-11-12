"""Add durable idempotency fingerprints to public guest appointments."""

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
    if not inspector.has_table("dental_appointments"):
        return

    columns = {column["name"] for column in inspector.get_columns("dental_appointments")}
    if "idempotency_key" not in columns:
        op.add_column(
            "dental_appointments",
            sa.Column("idempotency_key", sa.String(128), nullable=True),
        )
    if "idempotency_fingerprint" not in columns:
        op.add_column(
            "dental_appointments",
            sa.Column("idempotency_fingerprint", sa.String(64), nullable=True),
        )

    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("dental_appointments")}
    if "uq_dental_appointments_clinic_idempotency" not in indexes:
        op.create_index(
            "uq_dental_appointments_clinic_idempotency",
            "dental_appointments",
            ["clinic_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_appointments"):
        return

    indexes = {index["name"] for index in inspector.get_indexes("dental_appointments")}
    if "uq_dental_appointments_clinic_idempotency" in indexes:
        op.drop_index(
            "uq_dental_appointments_clinic_idempotency",
            table_name="dental_appointments",
        )

    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("dental_appointments")}
    if "idempotency_fingerprint" in columns:
        op.drop_column("dental_appointments", "idempotency_fingerprint")
    if "idempotency_key" in columns:
        op.drop_column("dental_appointments", "idempotency_key")
