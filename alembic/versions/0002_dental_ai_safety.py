from __future__ import annotations

import os
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_dental_ai_safety"
down_revision = "0001_dental_postgres_schema"
branch_labels = None
depends_on = None


def _ensure_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    names = {c["name"] for c in inspect(bind).get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    if not inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns)


def _ensure_index(name: str, table: str, columns: list[str]) -> None:
    if name not in {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    default_clinic = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"
    _ensure_column("dental_patients", sa.Column("user_id", sa.String(128), nullable=True))
    _ensure_index("idx_dental_patients_user", "dental_patients", ["user_id"])

    _ensure_table("dental_ai_cache", [
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_index("idx_dental_ai_cache_expires", "dental_ai_cache", ["expires_at"])

    _ensure_table("dental_ai_usage", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=default_clinic),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_index("idx_dental_ai_usage_actor_time", "dental_ai_usage", ["actor_id", "created_at"])
    _ensure_index("idx_dental_ai_usage_clinic_time", "dental_ai_usage", ["clinic_id", "created_at"])

    _ensure_table("dental_ai_approvals", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.Text(), nullable=False),
    ])

    _ensure_table("dental_patient_ai_consents", [
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    ])

    _ensure_table("dental_patient_ai_messages", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=True),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=default_clinic),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_index("idx_dental_patient_ai_messages_user", "dental_patient_ai_messages", ["user_id", "created_at"])

    _ensure_table("dental_patient_devices", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=True),
        sa.Column("clinic_id", sa.String(128), nullable=False, server_default=default_clinic),
        sa.Column("expo_push_token", sa.String(512), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    _ensure_index("idx_dental_patient_devices_user", "dental_patient_devices", ["user_id"])


def downgrade() -> None:
    for table in [
        "dental_patient_devices", "dental_patient_ai_messages",
        "dental_patient_ai_consents", "dental_ai_approvals",
        "dental_ai_usage", "dental_ai_cache",
    ]:
        op.drop_table(table)
    op.drop_column("dental_patients", "user_id")
