"""Create the dental persistence schema and add clinic isolation.

The migration is deliberately idempotent for the legacy SQLite schema that was
previously created by DentalStore.initialize(). Existing rows receive the
DENTAL_MASTER_CLINIC_ID (or dr-pranali) clinic id when no clinic id existed.
"""
from __future__ import annotations

import os
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0001_dental_postgres_schema"
down_revision = None
branch_labels = None
depends_on = None

DEFAULT_CLINIC = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(name):
        op.create_table(name, *columns)


def _ensure_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if column.name not in {c["name"] for c in inspector.get_columns(table)}:
        op.add_column(table, column)


def _ensure_index(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in inspect(bind).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _ensure_table("dental_patients", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False, server_default=""),
        sa.Column("age", sa.Text(), nullable=False, server_default=""),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_column("dental_patients", sa.Column("clinic_id", sa.String(128), nullable=True))
    op.execute(sa.text("UPDATE dental_patients SET clinic_id = :clinic WHERE clinic_id IS NULL OR clinic_id = ''").bindparams(clinic=DEFAULT_CLINIC))
    _ensure_index("idx_dental_patients_clinic", "dental_patients", ["clinic_id"])

    _ensure_table("dental_appointments", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("starts_at", sa.Text(), nullable=False),
        sa.Column("treatment_type", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_column("dental_appointments", sa.Column("clinic_id", sa.String(128), nullable=True))
    _ensure_column("dental_appointments", sa.Column("note", sa.Text(), nullable=True))
    op.execute(sa.text("UPDATE dental_appointments SET clinic_id = :clinic WHERE clinic_id IS NULL OR clinic_id = ''").bindparams(clinic=DEFAULT_CLINIC))
    _ensure_index("idx_dental_appointments_start", "dental_appointments", ["starts_at"])
    _ensure_index("idx_dental_appointments_clinic", "dental_appointments", ["clinic_id"])

    _ensure_table("dental_chart_entries", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("tooth_fdi", sa.String(8), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_column("dental_chart_entries", sa.Column("clinic_id", sa.String(128), nullable=True))
    op.execute(sa.text("UPDATE dental_chart_entries SET clinic_id = :clinic WHERE clinic_id IS NULL OR clinic_id = ''").bindparams(clinic=DEFAULT_CLINIC))
    _ensure_index("idx_dental_chart_patient", "dental_chart_entries", ["patient_id"])
    _ensure_index("idx_dental_chart_clinic_patient", "dental_chart_entries", ["clinic_id", "patient_id"])

    _ensure_table("dental_periodontal_entries", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("tooth_fdi", sa.String(8), nullable=False),
        sa.Column("measurements_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_column("dental_periodontal_entries", sa.Column("clinic_id", sa.String(128), nullable=True))
    op.execute(sa.text("UPDATE dental_periodontal_entries SET clinic_id = :clinic WHERE clinic_id IS NULL OR clinic_id = ''").bindparams(clinic=DEFAULT_CLINIC))
    _ensure_index("idx_dental_periodontal_patient", "dental_periodontal_entries", ["patient_id"])
    _ensure_index("idx_dental_periodontal_clinic_patient", "dental_periodontal_entries", ["clinic_id", "patient_id"])

    _ensure_table("dental_ai_events", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("patient_id", sa.String(64), nullable=True),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_column("dental_ai_events", sa.Column("clinic_id", sa.String(128), nullable=True))
    op.execute(sa.text("UPDATE dental_ai_events SET clinic_id = :clinic WHERE clinic_id IS NULL OR clinic_id = ''").bindparams(clinic=DEFAULT_CLINIC))

    _ensure_table("dental_otp_challenges", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("otp_hash", sa.Text(), nullable=False),
        sa.Column("challenge_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
    ])
    _ensure_index("idx_dental_otp_phone", "dental_otp_challenges", ["phone"])

    _ensure_table("dental_sessions", [
        sa.Column("token_hash", sa.String(128), primary_key=True),
        sa.Column("doctor_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    ])

    _ensure_table("dental_audit_events", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])

    _ensure_table("dental_doctors", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("phone", sa.Text(), nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="doctor"),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=DEFAULT_CLINIC),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])

    _ensure_table("dental_membership_orders", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("razorpay_order_id", sa.String(128), nullable=False),
        sa.Column("amount_inr", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(128), nullable=True),
        sa.Column("paid_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])

    _ensure_table("dental_memberships", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])


def downgrade() -> None:
    for table in [
        "dental_memberships", "dental_membership_orders", "dental_doctors",
        "dental_audit_events", "dental_sessions", "dental_otp_challenges",
        "dental_ai_events", "dental_periodontal_entries", "dental_chart_entries",
        "dental_appointments", "dental_patients",
    ]:
        op.drop_table(table)
