"""Add organisation/workspace registry and backfill legacy doctor memberships."""

from __future__ import annotations

import os
import uuid

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0006_multi_tenant_workspaces"
down_revision = "0005_password_auth"
branch_labels = None
depends_on = None

_VALID_ROLES = {"owner", "admin", "doctor", "dentist"}
_CLINIC_TABLES = (
    "dental_doctors",
    "dental_patients",
    "dental_appointments",
    "dental_chart_entries",
    "dental_periodontal_entries",
    "dental_ai_events",
)


def _stable_organisation_id(clinic_id: str) -> str:
    value = uuid.uuid5(
        uuid.NAMESPACE_URL, f"uie:dental:legacy-organisation:{clinic_id}"
    )
    return f"org-{value.hex}"


def _ensure_table(name: str, *columns: sa.Column | sa.Constraint) -> None:
    if not inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns)


def _legacy_clinic_ids(bind: sa.Connection) -> list[str]:
    clinic_ids: set[str] = set()
    inspector = inspect(bind)
    for table in _CLINIC_TABLES:
        if not inspector.has_table(table):
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "clinic_id" not in columns:
            continue
        rows = bind.execute(
            sa.text(
                f"SELECT DISTINCT clinic_id FROM {table} "
                "WHERE clinic_id IS NOT NULL AND TRIM(clinic_id) <> ''"
            )
        )
        clinic_ids.update(str(row[0]).strip() for row in rows if row[0])
    return sorted(clinic_ids)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_table(
        "dental_organisations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("organisation_type", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "organisation_type IN ('clinic', 'hospital', 'group')",
            name="ck_dental_organisations_type",
        ),
    )
    _ensure_table(
        "dental_clinics",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "organisation_id",
            sa.String(64),
            sa.ForeignKey("dental_organisations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("clinic_type", sa.String(32), nullable=False),
        sa.Column(
            "parent_clinic_id",
            sa.String(128),
            sa.ForeignKey("dental_clinics.id"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "clinic_type IN ('clinic', 'branch', 'hospital_department')",
            name="ck_dental_clinics_type",
        ),
    )
    _ensure_table(
        "dental_doctor_memberships",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "doctor_id",
            sa.String(64),
            sa.ForeignKey("dental_doctors.id"),
            nullable=False,
        ),
        sa.Column(
            "clinic_id",
            sa.String(128),
            sa.ForeignKey("dental_clinics.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "doctor_id", "clinic_id", name="uq_dental_doctor_membership"
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'doctor', 'dentist')",
            name="ck_dental_doctor_memberships_role",
        ),
    )

    clinic_ids = _legacy_clinic_ids(bind)
    configured_master_clinic_id = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()
    if configured_master_clinic_id:
        master_clinic_id = configured_master_clinic_id
    elif "dr-pranali" in clinic_ids:
        master_clinic_id = "dr-pranali"
    elif len(clinic_ids) == 1:
        # A single legacy clinic is unambiguous and becomes the first tenant.
        master_clinic_id = clinic_ids[0]
    elif len(clinic_ids) > 1:
        raise RuntimeError(
            "Multiple legacy clinic IDs exist. Set DENTAL_MASTER_CLINIC_ID "
            "to the Dr Pranali clinic ID before applying this migration."
        )
    else:
        master_clinic_id = "dr-pranali"

    master_clinic_name = (
        os.getenv("DENTAL_MASTER_CLINIC_NAME", "").strip()
        or "Dr Pranali Dental Clinic"
    )
    created_at = "1970-01-01T00:00:00+00:00"

    # Register every legacy workspace found in any clinic-scoped Dental table.
    # Clinical records are read only; this migration never updates or moves them.
    for clinic_id in clinic_ids:
        organisation_id = _stable_organisation_id(clinic_id)
        organisation_exists = bind.execute(
            sa.text("SELECT 1 FROM dental_organisations WHERE id = :id"),
            {"id": organisation_id},
        ).first()
        workspace_name = (
            master_clinic_name
            if clinic_id == master_clinic_id
            else f"Legacy workspace - {clinic_id}"
        )[:200]
        if not organisation_exists:
            bind.execute(
                sa.text(
                    "INSERT INTO dental_organisations "
                    "(id, name, organisation_type, active, created_at) "
                    "VALUES (:id, :name, 'clinic', :active, :created_at)"
                ),
                {
                    "id": organisation_id,
                    "name": master_clinic_name
                    if clinic_id == master_clinic_id
                    else f"Legacy organisation - {clinic_id}"[:200],
                    "active": True,
                    "created_at": created_at,
                },
            )

        workspace_exists = bind.execute(
            sa.text("SELECT 1 FROM dental_clinics WHERE id = :id"),
            {"id": clinic_id},
        ).first()
        if not workspace_exists:
            bind.execute(
                sa.text(
                    "INSERT INTO dental_clinics "
                    "(id, organisation_id, name, clinic_type, parent_clinic_id, active, created_at) "
                    "VALUES (:id, :organisation_id, :name, 'clinic', NULL, :active, :created_at)"
                ),
                {
                    "id": clinic_id,
                    "organisation_id": organisation_id,
                    "name": workspace_name,
                    "active": True,
                    "created_at": created_at,
                },
            )

    doctor_columns = {
        column["name"] for column in inspect(bind).get_columns("dental_doctors")
    }
    active_expression = "active" if "active" in doctor_columns else "1 AS active"
    doctors = bind.execute(
        sa.text(
            "SELECT id, clinic_id, role, created_at, "
            f"{active_expression} FROM dental_doctors "
            "WHERE clinic_id IS NOT NULL AND TRIM(clinic_id) <> ''"
        )
    )
    for doctor in doctors:
        doctor_id, clinic_id, legacy_role, doctor_created_at, doctor_active = doctor
        membership_exists = bind.execute(
            sa.text(
                "SELECT 1 FROM dental_doctor_memberships "
                "WHERE doctor_id = :doctor_id AND clinic_id = :clinic_id"
            ),
            {"doctor_id": doctor_id, "clinic_id": clinic_id},
        ).first()
        if membership_exists:
            continue
        role = str(legacy_role or "doctor").strip().lower()
        if role not in _VALID_ROLES:
            role = "doctor"
        membership_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"uie:dental:doctor-membership:{doctor_id}:{clinic_id}",
        ).hex
        bind.execute(
            sa.text(
                "INSERT INTO dental_doctor_memberships "
                "(id, doctor_id, clinic_id, role, active, created_at) "
                "VALUES (:id, :doctor_id, :clinic_id, :role, :active, :created_at)"
            ),
            {
                "id": membership_id,
                "doctor_id": doctor_id,
                "clinic_id": clinic_id,
                "role": role,
                "active": bool(doctor_active),
                "created_at": doctor_created_at or created_at,
            },
        )

    op.create_index(
        "idx_dental_clinics_organisation",
        "dental_clinics",
        ["organisation_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_dental_doctor_memberships_clinic_active",
        "dental_doctor_memberships",
        ["clinic_id", "active"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_dental_doctor_memberships_doctor_active",
        "dental_doctor_memberships",
        ["doctor_id", "active"],
        if_not_exists=True,
    )


def downgrade() -> None:
    # Keep tenant registry and memberships: dropping them would discard access
    # assignments and could orphan newly-created workspaces.
    pass
