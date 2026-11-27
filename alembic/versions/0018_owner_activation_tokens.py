"""Add one-time owner activation tokens for verified clinic onboarding."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0018_owner_activation_tokens"
down_revision = "0017_clinic_trial_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("dental_owner_activation_tokens"):
        op.create_table(
            "dental_owner_activation_tokens",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "doctor_id",
                sa.String(128),
                sa.ForeignKey("dental_doctors.id"),
                nullable=False,
            ),
            sa.Column(
                "clinic_id",
                sa.String(128),
                sa.ForeignKey("dental_clinics.id"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("used_at", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("token_hash", name="uq_dental_owner_activation_token_hash"),
        )
    inspector = inspect(bind)
    if inspector.has_table("dental_owner_activation_tokens"):
        indexes = {item["name"] for item in inspector.get_indexes("dental_owner_activation_tokens")}
        if "idx_dental_owner_activation_tokens_doctor_expiry" not in indexes:
            op.create_index(
                "idx_dental_owner_activation_tokens_doctor_expiry",
                "dental_owner_activation_tokens",
                ["doctor_id", "expires_at"],
            )


def downgrade() -> None:
    # Forward-only: preserve owner activation auditability.
    pass
