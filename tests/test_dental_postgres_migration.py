from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

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
            }.issubset(tables)
            audit_columns = {
                column["name"]
                for column in inspector.get_columns("dental_audit_events")
            }
            assert "clinic_id" in audit_columns

            workspaces = connection.execute(
                sa.text("SELECT id FROM dental_clinics")
            ).scalars().all()
            assert workspaces == ["legacy-pg-clinic"]

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
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with base_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base_engine.dispose()
