from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005_dental_doctor_auth_hardening"
down_revision = "0004_patient_invites_billing"
branch_labels = None
depends_on = None


def _ensure_column(table: str, column: sa.Column) -> None:
    inspector = inspect(op.get_bind())
    if column.name not in {c["name"] for c in inspector.get_columns(table)}:
        op.add_column(table, column)


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    if not inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns)


def upgrade() -> None:
    _ensure_column("dental_doctors", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    _ensure_column("dental_doctors", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    _ensure_column("dental_doctors", sa.Column("display_name", sa.Text(), nullable=False, server_default=""))
    _ensure_column("dental_doctors", sa.Column("password_changed_at", sa.Text(), nullable=True))

    _ensure_table("dental_login_audit", [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
    ])
    _ensure_table("dental_auth_throttle", [
        sa.Column("scope_type", sa.String(16), primary_key=True),
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    ])
    if inspect(op.get_bind()).has_table("dental_doctors"):
        indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("dental_doctors")}
        if "uq_dental_single_admin" not in indexes:
            op.create_index(
                "uq_dental_single_admin", "dental_doctors", ["role"], unique=True,
                postgresql_where=sa.text("role = 'admin'"),
                sqlite_where=sa.text("role = 'admin'"),
            )
    if inspect(op.get_bind()).has_table("dental_login_audit"):
        indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("dental_login_audit")}
        if "idx_dental_login_audit_time" not in indexes:
            op.create_index("idx_dental_login_audit_time", "dental_login_audit", ["occurred_at"])
        if "idx_dental_login_audit_email" not in indexes:
            op.create_index("idx_dental_login_audit_email", "dental_login_audit", ["email"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("dental_doctors"):
        indexes = {i["name"] for i in inspector.get_indexes("dental_doctors")}
        if "uq_dental_single_admin" in indexes:
            op.drop_index("uq_dental_single_admin", table_name="dental_doctors")
    if inspector.has_table("dental_auth_throttle"):
        op.drop_table("dental_auth_throttle")
    if inspector.has_table("dental_login_audit"):
        op.drop_table("dental_login_audit")
    columns = {c["name"] for c in inspector.get_columns("dental_doctors")}
    for column in ("password_changed_at", "display_name", "must_change_password", "is_active"):
        if column in columns:
            op.drop_column("dental_doctors", column)
