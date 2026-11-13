"""Require explicit clinician review before private documents can be shared.

Existing document metadata and visibility flags are preserved. Patient-facing
queries additionally require a non-null reviewed_at timestamp, so legacy
shared documents remain private until a clinician explicitly reviews them.
No document rows or object keys are deleted.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0011_document_clinical_review"
down_revision = "0010_clinical_notes_messaging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("dental_documents"):
        raise RuntimeError("dental_documents must exist before adding clinical review fields")
    columns = {column["name"] for column in inspector.get_columns("dental_documents")}
    if "reviewed_by_doctor_id" not in columns:
        op.add_column(
            "dental_documents",
            sa.Column("reviewed_by_doctor_id", sa.String(64), nullable=True),
        )
    if "reviewed_at" not in columns:
        op.add_column(
            "dental_documents",
            sa.Column("reviewed_at", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    # Forward-only: retain document review history and metadata on rollback.
    pass
