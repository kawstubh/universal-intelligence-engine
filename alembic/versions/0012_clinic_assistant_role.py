"""Add a separate, least-privilege Clinic Assistant identity and session store."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0012_clinic_assistant_role"
down_revision = "0011_document_clinical_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_clinic_assistants"):
        op.create_table(
            "dental_clinic_assistants",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column(
                "home_clinic_id",
                sa.String(128),
                sa.ForeignKey("dental_clinics.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("email", name="uq_dental_clinic_assistants_email"),
        )

    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_clinic_assistant_memberships"):
        op.create_table(
            "dental_clinic_assistant_memberships",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "assistant_id",
                sa.String(64),
                sa.ForeignKey("dental_clinic_assistants.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "clinic_id",
                sa.String(128),
                sa.ForeignKey("dental_clinics.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("role", sa.String(24), nullable=False, server_default="assistant"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint(
                "assistant_id",
                "clinic_id",
                name="uq_dental_clinic_assistant_membership",
            ),
            sa.CheckConstraint(
                "role = 'assistant'",
                name="ck_dental_clinic_assistant_memberships_role",
            ),
        )

    inspector = inspect(op.get_bind())
    if not inspector.has_table("dental_assistant_sessions"):
        op.create_table(
            "dental_assistant_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("assistant_id", sa.String(64), nullable=False),
            sa.Column("clinic_id", sa.String(128), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["assistant_id", "clinic_id"],
                [
                    "dental_clinic_assistant_memberships.assistant_id",
                    "dental_clinic_assistant_memberships.clinic_id",
                ],
                ondelete="RESTRICT",
                name="fk_dental_assistant_sessions_membership",
            ),
        )

    op.create_index(
        "idx_dental_assistant_memberships_clinic_active",
        "dental_clinic_assistant_memberships",
        ["clinic_id", "active"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_dental_assistant_sessions_assistant_clinic",
        "dental_assistant_sessions",
        ["assistant_id", "clinic_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    for table_name in (
        "dental_assistant_sessions",
        "dental_clinic_assistant_memberships",
        "dental_clinic_assistants",
    ):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
