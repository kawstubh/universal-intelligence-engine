"""Add private clinical notes and tenant-scoped patient/clinic messages."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0010_clinical_notes_messaging"
down_revision = "0009_private_dental_documents"
branch_labels = None
depends_on = None


def _ensure_index(name: str, table: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_clinical_notes"):
        op.create_table(
            "dental_clinical_notes",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), nullable=False),
            sa.Column("patient_id", sa.String(64), nullable=False),
            sa.Column("created_by_doctor_id", sa.String(64), nullable=False),
            sa.Column("title", sa.String(160), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("patient_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.CheckConstraint("length(title) >= 2", name="ck_dental_clinical_notes_title"),
            sa.CheckConstraint("length(content) > 0", name="ck_dental_clinical_notes_content"),
            sa.CheckConstraint("version >= 1", name="ck_dental_clinical_notes_version"),
        )
    _ensure_index(
        "idx_dental_clinical_notes_clinic_patient_created",
        "dental_clinical_notes",
        ["clinic_id", "patient_id", "created_at"],
    )
    _ensure_index(
        "idx_dental_clinical_notes_patient_visible",
        "dental_clinical_notes",
        ["clinic_id", "patient_id", "patient_visible"],
    )

    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_patient_messages"):
        op.create_table(
            "dental_patient_messages",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), nullable=False),
            sa.Column("patient_id", sa.String(64), nullable=False),
            sa.Column("sender_type", sa.String(16), nullable=False),
            sa.Column("sender_id", sa.String(128), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "sender_type IN ('doctor','patient')",
                name="ck_dental_patient_messages_sender_type",
            ),
            sa.CheckConstraint("length(body) BETWEEN 1 AND 4000", name="ck_dental_patient_messages_body"),
        )
    _ensure_index(
        "idx_dental_patient_messages_clinic_patient_created",
        "dental_patient_messages",
        ["clinic_id", "patient_id", "created_at"],
    )


def downgrade() -> None:
    # Forward-only: preserve clinical notes and patient communication history.
    pass
