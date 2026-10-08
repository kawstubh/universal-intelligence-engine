from __future__ import annotations

import hashlib
import secrets
import string
import time
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_patient_invites_billing"
down_revision = "0003_razorpay_idempotency"
branch_labels = None
depends_on = None


def _ensure_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if column.name not in {c["name"] for c in inspect(bind).get_columns(table)}:
        op.add_column(table, column)


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    if not inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns)


def upgrade() -> None:
    _ensure_column("dental_memberships", sa.Column("current_period_end", sa.Text(), nullable=True))
    _ensure_column("dental_memberships", sa.Column("cancelled_at", sa.Text(), nullable=True))
    _ensure_column("dental_memberships", sa.Column("updated_at", sa.Text(), nullable=True))

    _ensure_table("dental_patient_invites", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("code_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.Column("used_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    ])
    if "idx_dental_patient_invites_patient" not in {i["name"] for i in inspect(op.get_bind()).get_indexes("dental_patient_invites")}:
        op.create_index("idx_dental_patient_invites_patient", "dental_patient_invites", ["patient_id"])

    # OTP is no longer an application authentication mechanism.
    if inspect(op.get_bind()).has_table("dental_otp_challenges"):
        op.drop_table("dental_otp_challenges")


def downgrade() -> None:
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
    op.drop_table("dental_patient_invites")
    for column in ("updated_at", "cancelled_at", "current_period_end"):
        op.drop_column("dental_memberships", column)
