from __future__ import annotations

import os
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_public_clinic_profile"
down_revision = "0005_dental_doctor_auth_hardening"
branch_labels = None
depends_on = None

DEFAULT_CLINIC = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("dental_clinic_profiles"):
        op.create_table(
            "dental_clinic_profiles",
            sa.Column("clinic_id", sa.String(128), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("phone", sa.Text(), nullable=False),
            sa.Column("phone_e164", sa.Text(), nullable=False),
            sa.Column("whatsapp_phone", sa.Text(), nullable=False),
            sa.Column("address", sa.Text(), nullable=False, server_default=""),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    exists = bind.execute(
        sa.text("SELECT clinic_id FROM dental_clinic_profiles WHERE clinic_id=:clinic_id"),
        {"clinic_id": DEFAULT_CLINIC},
    ).first()
    if not exists:
        bind.execute(
            sa.text(
                "INSERT INTO dental_clinic_profiles "
                "(clinic_id,name,phone,phone_e164,whatsapp_phone,address,active,updated_at) "
                "VALUES (:clinic_id,:name,:phone,:phone_e164,:whatsapp_phone,:address,:active,:updated_at)"
            ),
            {
                "clinic_id": DEFAULT_CLINIC,
                "name": "Dr. Pranali Dental Clinic",
                "phone": "9137007432",
                "phone_e164": "+919137007432",
                "whatsapp_phone": "919137007432",
                "address": "Taloja, Navi Mumbai",
                "active": True,
                "updated_at": "2026-10-13T00:00:00+00:00",
            },
        )


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("dental_clinic_profiles"):
        op.drop_table("dental_clinic_profiles")
