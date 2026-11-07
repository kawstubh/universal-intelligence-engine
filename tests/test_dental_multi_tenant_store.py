from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> DentalStore:
    database_path = tmp_path / "membership-store.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    return DentalStore()


def _seed_doctor(store: DentalStore) -> None:
    store.create_doctor(
        {
            "id": "doctor-1",
            "email": "doctor@example.test",
            "password_hash": "not-a-real-password-hash",
            "clinic_id": "clinic-a",
            "role": "doctor",
        }
    )


def test_doctor_workspace_queries_return_only_active_memberships(store: DentalStore):
    store.create_organisation(
        {"id": "org-a", "name": "Clinic Group", "organisation_type": "group"}
    )
    store.create_organisation(
        {"id": "org-b", "name": "Partner Hospital", "organisation_type": "hospital"}
    )
    for clinic in (
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Main Clinic",
            "clinic_type": "clinic",
        },
        {
            "id": "clinic-b",
            "organisation_id": "org-a",
            "name": "Branch Clinic",
            "clinic_type": "branch",
        },
        {
            "id": "clinic-c",
            "organisation_id": "org-b",
            "name": "Dental Department",
            "clinic_type": "hospital_department",
        },
    ):
        store.create_clinic(clinic)
    _seed_doctor(store)
    store.create_doctor_membership("doctor-1", "clinic-a", "doctor")
    store.create_doctor_membership("doctor-1", "clinic-b", "dentist")
    store.create_doctor_membership("doctor-1", "clinic-c", "admin")

    workspaces = store.workspaces_for_doctor("doctor-1")
    assert [(item["id"], item["role"]) for item in workspaces] == [
        ("clinic-b", "dentist"),
        ("clinic-a", "doctor"),
        ("clinic-c", "admin"),
    ]
    organisations = store.organisations_for_doctor("doctor-1")
    assert [(item["id"], item["name"]) for item in organisations] == [
        ("org-a", "Clinic Group"),
        ("org-b", "Partner Hospital"),
    ]

    store.set_doctor_membership_active("doctor-1", "clinic-b", False)
    assert [item["id"] for item in store.workspaces_for_doctor("doctor-1")] == [
        "clinic-a",
        "clinic-c",
    ]
    membership = store.doctor_membership("doctor-1", "clinic-b")
    assert (
        membership["doctor_id"],
        membership["clinic_id"],
        membership["role"],
        membership["active"],
    ) == ("doctor-1", "clinic-b", "dentist", 0)


def test_create_membership_rejects_duplicate_doctor_workspace_pair(store: DentalStore):
    store.create_organisation(
        {"id": "org-a", "name": "Clinic Group", "organisation_type": "group"}
    )
    store.create_clinic(
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Main Clinic",
            "clinic_type": "clinic",
        }
    )
    _seed_doctor(store)
    store.create_doctor_membership("doctor-1", "clinic-a", "doctor")

    with pytest.raises(sqlite3.IntegrityError):
        store.create_doctor_membership("doctor-1", "clinic-a", "dentist")


def test_audit_event_records_workspace_context(store: DentalStore):
    store.audit(
        "doctor-1",
        "membership.created",
        "doctor_membership",
        "membership-1",
        clinic_id="clinic-a",
    )

    events = store._execute(
        "SELECT actor_id, action, resource_type, resource_id, clinic_id "
        "FROM dental_audit_events WHERE action=?",
        ("membership.created",),
    )
    assert events == [
        {
            "actor_id": "doctor-1",
            "action": "membership.created",
            "resource_type": "doctor_membership",
            "resource_id": "membership-1",
            "clinic_id": "clinic-a",
        }
    ]
