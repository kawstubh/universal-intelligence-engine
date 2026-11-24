"""Allow expired quarantine object keys to be cleared after safe deletion.

Only the pending upload key becomes nullable. Verified object keys and document
metadata are preserved. The migration is forward-only to avoid reintroducing
stale object references during rollback.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0014_document_quarantine_cleanup"
down_revision = "0013_public_booking_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_documents"):
        raise RuntimeError("dental_documents must exist before quarantine cleanup migration")
    columns = {column["name"]: column for column in inspector.get_columns("dental_documents")}
    if "upload_object_key" not in columns:
        raise RuntimeError("dental_documents.upload_object_key is required")
    if not columns["upload_object_key"]["nullable"]:
        with op.batch_alter_table("dental_documents") as batch:
            batch.alter_column(
                "upload_object_key",
                existing_type=sa.String(length=1024),
                nullable=True,
            )


def downgrade() -> None:
    # Forward-only: a cleaned quarantine key must not be recreated or guessed.
    pass
