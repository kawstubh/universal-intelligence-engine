"""Add clinic SaaS entitlement and onboarding request state."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0017_clinic_trial_entitlements"
down_revision = "0016_shared_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("dental_clinic_entitlements"):
        op.create_table(
            "dental_clinic_entitlements",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "clinic_id",
                sa.String(128),
                sa.ForeignKey("dental_clinics.id"),
                nullable=False,
            ),
            sa.Column("entitlement_type", sa.String(24), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("plan_id", sa.String(80), nullable=True),
            sa.Column("trial_started_at", sa.BigInteger(), nullable=True),
            sa.Column("trial_ends_at", sa.BigInteger(), nullable=True),
            sa.Column("current_period_end", sa.BigInteger(), nullable=True),
            sa.Column("source_order_id", sa.String(128), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("clinic_id", name="uq_dental_clinic_entitlement_clinic"),
            sa.CheckConstraint(
                "entitlement_type IN ('founding_free','trial','subscription','manual')",
                name="ck_dental_clinic_entitlement_type",
            ),
            sa.CheckConstraint(
                "status IN ('trialing','active','past_due','cancelled','expired','paused','lifetime_free')",
                name="ck_dental_clinic_entitlement_status",
            ),
            sa.CheckConstraint(
                "(trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
                "(trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL AND trial_ends_at > trial_started_at)",
                name="ck_dental_clinic_entitlement_trial_window",
            ),
        )

    if not inspector.has_table("dental_onboarding_requests"):
        op.create_table(
            "dental_onboarding_requests",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("request_key_hash", sa.String(64), nullable=False),
            sa.Column("clinic_name", sa.String(200), nullable=False),
            sa.Column("owner_name", sa.String(200), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("phone", sa.String(40), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=True),
            sa.Column("verified_at", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("request_key_hash", name="uq_dental_onboarding_request_key"),
            sa.CheckConstraint(
                "status IN ('pending','verified','provisioned','rejected')",
                name="ck_dental_onboarding_request_status",
            ),
        )

    inspector = inspect(bind)
    if inspector.has_table("dental_clinic_entitlements"):
        indexes = {item["name"] for item in inspector.get_indexes("dental_clinic_entitlements")}
        if "idx_dental_clinic_entitlements_status_trial_end" not in indexes:
            op.create_index(
                "idx_dental_clinic_entitlements_status_trial_end",
                "dental_clinic_entitlements",
                ["status", "trial_ends_at"],
            )

    if inspector.has_table("dental_onboarding_requests"):
        indexes = {item["name"] for item in inspector.get_indexes("dental_onboarding_requests")}
        if "idx_dental_onboarding_requests_status_created" not in indexes:
            op.create_index(
                "idx_dental_onboarding_requests_status_created",
                "dental_onboarding_requests",
                ["status", "created_at"],
            )


def downgrade() -> None:
    # Forward-only: retain onboarding and entitlement history.
    pass
