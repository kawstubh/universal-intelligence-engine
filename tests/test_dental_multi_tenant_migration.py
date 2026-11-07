from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def _seed_legacy_database(database_path: Path) -> None:
    os.environ.pop("DATABASE_URL", None)
    os.environ["DENTAL_SQLITE_PATH"] = str(database_path)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0005_password_auth")

    engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO dental_doctors "
                "(id,email,phone,password_hash,role,clinic_id,active,created_at) "
                "VALUES (:id,:email,:phone,:password_hash,:role,:clinic_id,:active,:created_at)"
            ),
            [
                {
                    "id": "doc-a",
                    "email": "a@example.test",
                    "phone": "",
                    "password_hash": "hash-a",
                    "role": "doctor",
                    "clinic_id": "dr-pranali",
                    "active": True,
                    "created_at": "2026-01-01",
                },
                {
                    "id": "doc-b",
                    "email": "b@example.test",
                    "phone": "",
                    "password_hash": "hash-b",
                    "role": "dentist",
                    "clinic_id": "branch-b",
                    "active": True,
                    "created_at": "2026-01-02",
                },
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO dental_patients "
                "(id,name,phone,age,clinic_id,created_at) "
                "VALUES (:id,:name,'','',:clinic,'2026-01-03')"
            ),
            [
                {"id": "patient-a", "name": "Patient A", "clinic": "dr-pranali"},
                {"id": "patient-b", "name": "Patient B", "clinic": "branch-b"},
                {
                    "id": "patient-c",
                    "name": "Archived Patient",
                    "clinic": "archive-only",
                },
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO dental_appointments "
                "(id,patient_id,clinic_id,starts_at,treatment_type,note,status,created_at) "
                "VALUES (:id,:patient,:clinic,'2026-01-04','Checkup','','scheduled','2026-01-03')"
            ),
            [
                {"id": "appt-a", "patient": "patient-a", "clinic": "dr-pranali"},
                {"id": "appt-b", "patient": "patient-b", "clinic": "branch-b"},
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO dental_chart_entries "
                "(id,patient_id,clinic_id,tooth_fdi,status,note,created_at) "
                "VALUES (:id,:patient,:clinic,'11','healthy','','2026-01-03')"
            ),
            [
                {"id": "chart-a", "patient": "patient-a", "clinic": "dr-pranali"},
                {"id": "chart-b", "patient": "patient-b", "clinic": "branch-b"},
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO dental_periodontal_entries "
                "(id,patient_id,clinic_id,tooth_fdi,measurements_json,note,created_at) "
                "VALUES (:id,:patient,:clinic,'11','{}','','2026-01-03')"
            ),
            [
                {"id": "perio-a", "patient": "patient-a", "clinic": "dr-pranali"},
                {"id": "perio-b", "patient": "patient-b", "clinic": "branch-b"},
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO dental_ai_events "
                "(id,patient_id,clinic_id,goal,result_json,created_at) "
                "VALUES (:id,:patient,:clinic,'summary','{}','2026-01-03')"
            ),
            [
                {"id": "ai-a", "patient": "patient-a", "clinic": "dr-pranali"},
                {"id": "ai-b", "patient": "patient-b", "clinic": "branch-b"},
            ],
        )
    engine.dispose()


def test_workspace_migration_backfills_memberships_without_rewriting_clinical_rows(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "legacy-dental.sqlite3"
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _seed_legacy_database(database_path)

    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # Re-running the migration command is a no-op.

    engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        required_tables = {
            "dental_organisations",
            "dental_clinics",
            "dental_doctor_memberships",
        }
        assert required_tables.issubset(set(inspector.get_table_names()))

        workspaces = connection.execute(
            sa.text("SELECT id, organisation_id FROM dental_clinics ORDER BY id")
        ).all()
        assert [row.id for row in workspaces] == [
            "archive-only",
            "branch-b",
            "dr-pranali",
        ]
        assert len({row.organisation_id for row in workspaces}) == 3

        memberships = connection.execute(
            sa.text(
                "SELECT doctor_id, clinic_id, role, active "
                "FROM dental_doctor_memberships ORDER BY doctor_id"
            )
        ).all()
        assert [
            (row.doctor_id, row.clinic_id, row.role, row.active)
            for row in memberships
        ] == [
            ("doc-a", "dr-pranali", "doctor", 1),
            ("doc-b", "branch-b", "dentist", 1),
        ]

        expected_ids = {
            "dental_patients": ["patient-a", "patient-b", "patient-c"],
            "dental_appointments": ["appt-a", "appt-b"],
            "dental_chart_entries": ["chart-a", "chart-b"],
            "dental_periodontal_entries": ["perio-a", "perio-b"],
            "dental_ai_events": ["ai-a", "ai-b"],
        }
        for table, expected in expected_ids.items():
            actual = connection.execute(
                sa.text(f"SELECT id FROM {table} ORDER BY id")
            ).scalars().all()
            assert actual == expected
    engine.dispose()
