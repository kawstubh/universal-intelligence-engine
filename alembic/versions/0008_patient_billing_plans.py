"""Add tenant-scoped treatment plans, invoices and receipts.

Money is stored as integer paise. Patient visibility is opt-in and draft
financial records remain private to the clinic until explicitly issued.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0008_patient_billing_plans"
down_revision = "0007_dental_audit_workspace"
branch_labels = None
depends_on = None


def _ensure_table(name: str, *columns: sa.Column, **kwargs) -> None:
    if not inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns, **kwargs)


def _ensure_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    _ensure_table(
        "dental_treatment_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("created_by_doctor_id", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("patient_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','proposed','accepted','in_progress','completed','cancelled')",
            name="ck_dental_treatment_plans_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_dental_treatment_plans_version"),
    )
    _ensure_index(
        "idx_dental_treatment_plans_clinic_patient_created",
        "dental_treatment_plans",
        ["clinic_id", "patient_id", "created_at"],
    )
    _ensure_index(
        "idx_dental_treatment_plans_patient_visible",
        "dental_treatment_plans",
        ["clinic_id", "patient_id", "patient_visible"],
    )

    _ensure_table(
        "dental_treatment_plan_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("service_code", sa.String(80), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tooth_fdi", sa.String(8), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','approved','in_progress','completed','declined')",
            name="ck_dental_treatment_plan_items_status",
        ),
    )
    _ensure_index(
        "idx_dental_treatment_plan_items_plan",
        "dental_treatment_plan_items",
        ["clinic_id", "plan_id", "sort_order"],
    )

    _ensure_table(
        "dental_invoices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("created_by_doctor_id", sa.String(64), nullable=False),
        sa.Column("invoice_number", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("subtotal_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("discount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("paid_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("patient_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("patient_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("due_date", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("currency = 'INR'", name="ck_dental_invoices_currency"),
        sa.CheckConstraint(
            "status IN ('draft','issued','partially_paid','paid','overdue','void')",
            name="ck_dental_invoices_status",
        ),
        sa.CheckConstraint(
            "subtotal_paise >= 0 AND discount_paise >= 0 AND tax_paise >= 0 "
            "AND total_paise >= 0 AND paid_paise >= 0 AND paid_paise <= total_paise",
            name="ck_dental_invoices_amounts",
        ),
    )
    _ensure_index(
        "uq_dental_invoices_clinic_number",
        "dental_invoices",
        ["clinic_id", "invoice_number"],
        unique=True,
    )
    _ensure_index(
        "uq_dental_invoices_idempotency",
        "dental_invoices",
        ["clinic_id", "idempotency_key"],
        unique=True,
    )
    _ensure_index(
        "idx_dental_invoices_clinic_patient_created",
        "dental_invoices",
        ["clinic_id", "patient_id", "created_at"],
    )
    _ensure_index(
        "idx_dental_invoices_patient_visible",
        "dental_invoices",
        ["clinic_id", "patient_id", "patient_visible", "status"],
    )

    _ensure_table(
        "dental_invoice_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("invoice_id", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity_milli", sa.Integer(), nullable=False),
        sa.Column("unit_price_paise", sa.BigInteger(), nullable=False),
        sa.Column("tax_rate_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subtotal_paise", sa.BigInteger(), nullable=False),
        sa.Column("discount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_paise", sa.BigInteger(), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "quantity_milli > 0 AND unit_price_paise >= 0 AND "
            "tax_rate_bps >= 0 AND tax_rate_bps <= 2800 AND "
            "subtotal_paise >= 0 AND discount_paise >= 0 AND discount_paise <= subtotal_paise AND tax_paise >= 0 AND total_paise >= 0",
            name="ck_dental_invoice_items_amounts",
        ),
    )
    _ensure_index(
        "idx_dental_invoice_items_invoice",
        "dental_invoice_items",
        ["clinic_id", "invoice_id", "sort_order"],
    )

    _ensure_table(
        "dental_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("clinic_id", sa.String(128), nullable=False),
        sa.Column("invoice_id", sa.String(64), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("receipt_number", sa.String(80), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("reference", sa.String(128), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("paid_at", sa.Text(), nullable=False),
        sa.Column("created_by_doctor_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_dental_receipts_amount"),
        sa.CheckConstraint(
            "method IN ('cash','upi','bank_transfer','razorpay','other')",
            name="ck_dental_receipts_method",
        ),
    )
    _ensure_index(
        "uq_dental_receipts_clinic_number",
        "dental_receipts",
        ["clinic_id", "receipt_number"],
        unique=True,
    )
    _ensure_index(
        "uq_dental_receipts_idempotency",
        "dental_receipts",
        ["clinic_id", "idempotency_key"],
        unique=True,
    )
    _ensure_index(
        "idx_dental_receipts_clinic_invoice",
        "dental_receipts",
        ["clinic_id", "invoice_id", "paid_at"],
    )


def downgrade() -> None:
    # Forward-only: retain clinical and financial history if a deployment is rolled back.
    pass
