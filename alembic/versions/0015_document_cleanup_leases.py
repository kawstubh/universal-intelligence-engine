"""Persist exclusive, expiring leases for private document cleanup workers."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0015_document_cleanup_leases"
down_revision = "0014_document_quarantine_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_documents"):
        raise RuntimeError("dental_documents must exist before cleanup lease migration")
    columns = {column["name"] for column in inspector.get_columns("dental_documents")}
    if "cleanup_claim_token" not in columns:
        op.add_column(
            "dental_documents",
            sa.Column("cleanup_claim_token", sa.String(length=64), nullable=True),
        )
    if "cleanup_claimed_at" not in columns:
        op.add_column(
            "dental_documents",
            sa.Column("cleanup_claimed_at", sa.Text(), nullable=True),
        )
    indexes = {
        item["name"] for item in inspect(op.get_bind()).get_indexes("dental_documents")
    }
    if "idx_dental_documents_cleanup_lease" not in indexes:
        op.create_index(
            "idx_dental_documents_cleanup_lease",
            "dental_documents",
            ["status", "cleanup_claimed_at", "updated_at"],
        )


def downgrade() -> None:
    # Forward-only: never discard in-flight cleanup ownership during rollback.
    pass
