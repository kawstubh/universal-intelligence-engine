"""Add tenant-scoped dental inventory, suppliers and immutable stock movements."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0019_inventory_procurement_foundation"
down_revision = "0018_owner_activation_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("dental_inventory_suppliers"):
        op.create_table(
            "dental_inventory_suppliers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("contact_name", sa.String(160), nullable=False, server_default=""),
            sa.Column("phone", sa.String(40), nullable=False, server_default=""),
            sa.Column("email", sa.String(320), nullable=False, server_default=""),
            sa.Column("gstin", sa.String(20), nullable=False, server_default=""),
            sa.Column("address", sa.Text(), nullable=False, server_default=""),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.UniqueConstraint("clinic_id", "name", name="uq_inventory_supplier_clinic_name"),
        )
    inspector = inspect(bind)
    if not inspector.has_table("dental_inventory_items"):
        op.create_table(
            "dental_inventory_items",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("supplier_id", sa.String(64), sa.ForeignKey("dental_inventory_suppliers.id"), nullable=True),
            sa.Column("sku", sa.String(80), nullable=False, server_default=""),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("category", sa.String(80), nullable=False, server_default="general"),
            sa.Column("unit", sa.String(30), nullable=False, server_default="unit"),
            sa.Column("reorder_level", sa.Numeric(12, 3), nullable=False, server_default="0"),
            sa.Column("quantity_on_hand", sa.Numeric(12, 3), nullable=False, server_default="0"),
            sa.Column("default_unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.Column("updated_at", sa.String(40), nullable=False),
            sa.UniqueConstraint("clinic_id", "sku", name="uq_inventory_item_clinic_sku"),
        )
    inspector = inspect(bind)
    if not inspector.has_table("dental_inventory_movements"):
        op.create_table(
            "dental_inventory_movements",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(128), sa.ForeignKey("dental_clinics.id"), nullable=False),
            sa.Column("item_id", sa.String(64), sa.ForeignKey("dental_inventory_items.id"), nullable=False),
            sa.Column("supplier_id", sa.String(64), sa.ForeignKey("dental_inventory_suppliers.id"), nullable=True),
            sa.Column("movement_type", sa.String(20), nullable=False),
            sa.Column("quantity_delta", sa.Numeric(12, 3), nullable=False),
            sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("batch_number", sa.String(100), nullable=False, server_default=""),
            sa.Column("expires_on", sa.String(10), nullable=True),
            sa.Column("reference_type", sa.String(40), nullable=False, server_default=""),
            sa.Column("reference_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("reason", sa.String(500), nullable=False, server_default=""),
            sa.Column("actor_id", sa.String(128), nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.CheckConstraint("quantity_delta != 0", name="ck_inventory_movement_nonzero"),
            sa.CheckConstraint("movement_type IN ('receipt','consumption','adjustment','return')", name="ck_inventory_movement_type"),
        )
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("dental_inventory_items")}
    if "idx_inventory_items_clinic_active" not in indexes:
        op.create_index("idx_inventory_items_clinic_active", "dental_inventory_items", ["clinic_id", "active"])
    indexes = {item["name"] for item in inspector.get_indexes("dental_inventory_movements")}
    if "idx_inventory_movements_clinic_item_created" not in indexes:
        op.create_index("idx_inventory_movements_clinic_item_created", "dental_inventory_movements", ["clinic_id", "item_id", "created_at"])
    if "idx_inventory_movements_expiry" not in indexes:
        op.create_index("idx_inventory_movements_expiry", "dental_inventory_movements", ["clinic_id", "expires_on"])


def downgrade() -> None:
    # Forward-only: stock movement history is retained for financial auditability.
    pass
