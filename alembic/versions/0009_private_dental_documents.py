"""Add private clinic document metadata.

File bytes live in a private S3-compatible object store; the database stores
only tenant-scoped metadata and object keys. The migration never creates a
public bucket and the application refuses storage operations until explicitly
configured for a private bucket.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0009_private_dental_documents"
down_revision = "0008_patient_billing_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table("dental_documents"):
        op.create_table(
            "dental_documents",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("organisation_id", sa.String(128), nullable=False),
            sa.Column("clinic_id", sa.String(128), nullable=False),
            sa.Column("patient_id", sa.String(64), nullable=False),
            sa.Column("uploaded_by_doctor_id", sa.String(64), nullable=False),
            sa.Column("file_name", sa.String(180), nullable=False),
            sa.Column("content_type", sa.String(100), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("checksum_sha256", sa.String(64), nullable=False),
            sa.Column("object_key", sa.String(1024), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending_upload"),
            sa.Column("patient_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("uploaded_at", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending_upload','available','rejected')",
                name="ck_dental_documents_status",
            ),
            sa.CheckConstraint(
                "size_bytes > 0 AND size_bytes <= 10485760",
                name="ck_dental_documents_size",
            ),
            sa.CheckConstraint(
                "length(checksum_sha256) = 64",
                name="ck_dental_documents_checksum_length",
            ),
        )
    indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes("dental_documents")}
    if "uq_dental_documents_object_key" not in indexes:
        op.create_index(
            "uq_dental_documents_object_key",
            "dental_documents",
            ["object_key"],
            unique=True,
        )
    if "idx_dental_documents_clinic_patient_created" not in indexes:
        op.create_index(
            "idx_dental_documents_clinic_patient_created",
            "dental_documents",
            ["clinic_id", "patient_id", "created_at"],
        )
    if "idx_dental_documents_patient_visibility" not in indexes:
        op.create_index(
            "idx_dental_documents_patient_visibility",
            "dental_documents",
            ["clinic_id", "patient_id", "patient_visible", "status"],
        )


def downgrade() -> None:
    # Forward-only: never delete clinical documents or their metadata on rollback.
    pass
