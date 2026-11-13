from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from alembic import command
from universal_intelligence_engine.dental_store import DentalStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="PostgreSQL migration rehearsal runs in the CI PostgreSQL service",
)
def test_postgres_migrations_backfill_workspaces_without_touching_clinical_rows(
    monkeypatch,
):
    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    schema = f"dental_mt_{uuid.uuid4().hex[:12]}"
    base_engine = sa.create_engine(base_url)
    scoped_url = base_url.set(
        query={**dict(base_url.query), "options": f"-csearch_path={schema}"}
    )
    scoped_url_text = scoped_url.render_as_string(hide_password=False)
    schema_engine = None

    try:
        with base_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))

        monkeypatch.setenv("DATABASE_URL", scoped_url_text)
        monkeypatch.delenv("DENTAL_SQLITE_PATH", raising=False)
        config = Config(str(ROOT / "alembic.ini"))
        assert ScriptDirectory.from_config(config).get_heads() == [
            "0011_document_clinical_review"
        ]
        command.upgrade(config, "0005_password_auth")

        schema_engine = sa.create_engine(scoped_url)
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO dental_doctors "
                    "(id,email,phone,password_hash,role,clinic_id,active,created_at) "
                    "VALUES ('doctor-pg','pg@example.test','','hash','dentist',"
                    "'legacy-pg-clinic',true,'2026-01-01')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO dental_patients "
                    "(id,name,phone,age,clinic_id,created_at) "
                    "VALUES ('patient-pg','Patient PG','','','legacy-pg-clinic','2026-01-02')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO dental_appointments "
                    "(id,patient_id,clinic_id,starts_at,treatment_type,note,status,created_at) "
                    "VALUES ('appointment-pg','patient-pg','legacy-pg-clinic',"
                    "'2026-01-03','Checkup','','scheduled','2026-01-02')"
                )
            )

        command.upgrade(config, "head")

        with schema_engine.connect() as connection:
            inspector = sa.inspect(connection)
            tables = set(inspector.get_table_names())
            assert {
                "dental_organisations",
                "dental_clinics",
                "dental_doctor_memberships",
                "dental_treatment_plans",
                "dental_treatment_plan_items",
                "dental_invoices",
                "dental_invoice_items",
                "dental_receipts",
                "dental_documents",
                "dental_clinical_notes",
                "dental_patient_messages",
            }.issubset(tables)
            audit_columns = {
                column["name"]
                for column in inspector.get_columns("dental_audit_events")
            }
            assert "clinic_id" in audit_columns
            document_columns = {
                column["name"]
                for column in inspector.get_columns("dental_documents")
            }
            assert {"reviewed_by_doctor_id", "reviewed_at"}.issubset(document_columns)

            workspaces = connection.execute(
                sa.text("SELECT id FROM dental_clinics")
            ).scalars().all()
            assert workspaces == ["legacy-pg-clinic"]
            organisation = connection.execute(
                sa.text("SELECT name FROM dental_organisations")
            ).scalar_one()
            workspace_name = connection.execute(
                sa.text("SELECT name FROM dental_clinics")
            ).scalar_one()
            assert organisation == "Dr Pranali Dental Clinic"
            assert workspace_name == "Dr Pranali Dental Clinic"

            memberships = connection.execute(
                sa.text(
                    "SELECT doctor_id, clinic_id, role, active "
                    "FROM dental_doctor_memberships"
                )
            ).all()
            assert [
                (row.doctor_id, row.clinic_id, row.role, row.active)
                for row in memberships
            ] == [("doctor-pg", "legacy-pg-clinic", "dentist", True)]

            assert connection.execute(
                sa.text("SELECT id FROM dental_patients")
            ).scalars().all() == ["patient-pg"]
            assert connection.execute(
                sa.text("SELECT id FROM dental_appointments")
            ).scalars().all() == ["appointment-pg"]

        # SQLAlchemy's test URL uses the +psycopg driver suffix; DentalStore
        # connects with psycopg directly and therefore needs libpq's plain URL scheme.
        psycopg_url = scoped_url.set(drivername="postgresql")
        monkeypatch.setenv(
            "DATABASE_URL", psycopg_url.render_as_string(hide_password=False)
        )
        store = DentalStore()
        home_workspace = store._execute(
            "SELECT organisation_id FROM dental_clinics WHERE id=%s",
            ("legacy-pg-clinic",),
        )[0]
        home_organisation_id = home_workspace["organisation_id"]
        store.create_doctor(
            {
                "id": "doctor-admin-pg",
                "email": "pg-admin@example.test",
                "password_hash": "test-hash",
                "role": "admin",
                "clinic_id": "legacy-pg-clinic",
            }
        )
        store.create_doctor_membership(
            "doctor-admin-pg", "legacy-pg-clinic", "admin"
        )
        store.create_organisation(
            {
                "id": "org-pg-partner",
                "name": "Partner Hospital",
                "organisation_type": "hospital",
            }
        )
        store.create_clinic(
            {
                "id": "partner-pg-clinic",
                "organisation_id": "org-pg-partner",
                "name": "Partner Department",
                "clinic_type": "hospital_department",
            }
        )
        # Simulate a corrupted membership row that bypassed API tenant checks.
        store.create_doctor_membership(
            "doctor-pg", "partner-pg-clinic", "dentist"
        )
        assert store.workspaces_for_organisation("org-pg-partner")[0][
            "active_doctors"
        ] == 0
        assert store.workspaces_for_organisation(home_organisation_id)[0][
            "active_doctors"
        ] == 1

        # A corrupted cross-organisation membership must not grant the doctor
        # a second workspace or organisation during login/workspace discovery.
        assert [w["id"] for w in store.workspaces_for_doctor("doctor-pg")] == [
            "legacy-pg-clinic"
        ]
        assert [o["id"] for o in store.organisations_for_doctor("doctor-pg")] == [
            home_organisation_id
        ]

        # A legitimate owner of a newly created organisation may have an
        # original workspace in another organisation. The creation audit is
        # the provenance that makes this membership valid and countable.
        with store.transaction() as connection:
            store.create_organisation(
                {
                    "id": "org-pg-owner",
                    "name": "Owner Organisation",
                    "organisation_type": "group",
                },
                connection=connection,
            )
            store.create_clinic(
                {
                    "id": "owner-pg-clinic",
                    "organisation_id": "org-pg-owner",
                    "name": "Owner First Workspace",
                    "clinic_type": "clinic",
                },
                connection=connection,
            )
            store.create_doctor_membership(
                "doctor-pg", "owner-pg-clinic", "owner", connection=connection
            )
            store.audit(
                "doctor-pg",
                "organisation.created",
                "organisation",
                "org-pg-owner",
                clinic_id="legacy-pg-clinic",
                connection=connection,
            )
        assert store.workspaces_for_organisation("org-pg-owner")[0][
            "active_doctors"
        ] == 1
        assert "owner-pg-clinic" in {
            workspace["id"] for workspace in store.workspaces_for_doctor("doctor-pg")
        }
        assert "org-pg-owner" in {
            organisation["id"]
            for organisation in store.organisations_for_doctor("doctor-pg")
        }

        with pytest.raises(RuntimeError, match="simulated PostgreSQL rollback"), store.transaction() as connection:
            store.create_organisation(
                {
                    "id": "org-pg-rollback",
                    "name": "Rollback Organisation",
                    "organisation_type": "group",
                },
                connection=connection,
            )
            store.create_clinic(
                {
                    "id": "clinic-pg-rollback",
                    "organisation_id": "org-pg-rollback",
                    "name": "Rollback Workspace",
                    "clinic_type": "clinic",
                },
                connection=connection,
            )
            store.create_doctor_membership(
                "doctor-pg",
                "clinic-pg-rollback",
                "dentist",
                connection=connection,
            )
            store.audit(
                "doctor-pg",
                "workspace.created",
                "clinic",
                "clinic-pg-rollback",
                clinic_id="legacy-pg-clinic",
                connection=connection,
            )
            raise RuntimeError("simulated PostgreSQL rollback")

        assert store._execute(
            "SELECT id FROM dental_organisations WHERE id=%s",
            ("org-pg-rollback",),
        ) == []
        assert store._execute(
            "SELECT id FROM dental_clinics WHERE id=%s",
            ("clinic-pg-rollback",),
        ) == []
        assert store._execute(
            "SELECT id FROM dental_doctor_memberships WHERE clinic_id=%s",
            ("clinic-pg-rollback",),
        ) == []
        assert store._execute(
            "SELECT id FROM dental_audit_events WHERE resource_id=%s",
            ("clinic-pg-rollback",),
        ) == []
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with base_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base_engine.dispose()
