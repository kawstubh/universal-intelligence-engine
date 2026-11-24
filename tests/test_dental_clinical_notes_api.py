from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def clinical_notes_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "clinical-notes.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    monkeypatch.setattr(dental_store, "_store", None)
    store = DentalStore()
    for org_id, clinic_id, name in (
        ("org-notes-a", "clinic-notes-a", "Clinic A"),
        ("org-notes-b", "clinic-notes-b", "Clinic B"),
    ):
        store.create_organisation(
            {"id": org_id, "name": name, "organisation_type": "clinic"}
        )
        store.create_clinic(
            {
                "id": clinic_id,
                "organisation_id": org_id,
                "name": name,
                "clinic_type": "clinic",
            }
        )
    patient_a = store.create_patient(
        {"name": "Synthetic Patient A", "phone": "9000000001", "age": "35"},
        "clinic-notes-a",
    )
    patient_b = store.create_patient(
        {"name": "Synthetic Patient B", "phone": "9000000002", "age": "29"},
        "clinic-notes-b",
    )
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    app = FastAPI()
    app.include_router(dental_api.router)
    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-a",
        "user_id": "doctor-a",
        "email": "doctor@example.test",
        "role": "doctor",
        "clinic_id": "clinic-notes-a",
    }
    return TestClient(app), store, patient_a, patient_b


def test_doctor_can_create_and_list_private_clinical_notes(clinical_notes_api):
    client, store, patient, _other = clinical_notes_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes",
        json={"title": "Initial examination", "content": "Doctor-only clinical note."},
    )
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["patient_id"] == patient["id"]
    assert note["clinic_id"] == "clinic-notes-a"
    assert note["patient_visible"] is False
    assert note["version"] == 1

    listed = client.get(
        f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes"
    )
    assert listed.status_code == 200
    assert listed.json()["notes"] == [note]
    audit = store._execute(
        "SELECT action,resource_type,resource_id,metadata_json "
        "FROM dental_audit_events WHERE resource_type='clinical_note'"
    )
    assert audit and audit[0]["action"] == "clinical_note.created"
    assert "Doctor-only clinical note" not in audit[0]["metadata_json"]


def test_clinical_notes_reject_cross_clinic_patient_and_note_reads(clinical_notes_api):
    client, store, _patient_a, patient_b = clinical_notes_api
    foreign_patient = client.get(
        f"/v1/dental/doctor/patients/{patient_b['id']}/clinical-notes"
    )
    assert foreign_patient.status_code == 404

    foreign_note = store.create_clinical_note(
        patient_id=patient_b["id"],
        clinic_id="clinic-notes-b",
        doctor_id="doctor-b",
        title="Private note",
        content="Must remain isolated.",
    )
    update = client.patch(
        f"/v1/dental/doctor/clinical-notes/{foreign_note['id']}",
        json={
            "title": "Attempted access",
            "content": "Must not cross clinic boundaries.",
            "expected_version": 1,
        },
    )
    assert update.status_code == 404


def test_clinical_note_updates_require_matching_version(clinical_notes_api):
    client, _store, patient, _other = clinical_notes_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes",
        json={"title": "Review", "content": "First version."},
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]

    updated = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note_id}",
        json={
            "title": "Review updated",
            "content": "Second version.",
            "expected_version": 1,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    stale = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note_id}",
        json={
            "title": "Stale update",
            "content": "This must not overwrite version two.",
            "expected_version": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "CLINICAL_NOTE_VERSION_CONFLICT"
    latest = client.get(
        f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes"
    ).json()["notes"][0]
    assert latest["content"] == "Second version."
    assert latest["version"] == 2


def test_clinical_note_content_validation_and_authentication(clinical_notes_api):
    client, _store, patient, _other = clinical_notes_api
    invalid = client.post(
        f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes",
        json={"title": "x", "content": ""},
    )
    assert invalid.status_code == 422


def test_doctor_messages_are_persisted_and_clinic_scoped(clinical_notes_api):
    client, store, patient, other_patient = clinical_notes_api
    sent = client.post(
        f"/v1/dental/doctor/patients/{patient['id']}/messages",
        json={"body": "Please confirm your next appointment."},
    )
    assert sent.status_code == 201, sent.text
    message = sent.json()
    assert message["sender_type"] == "doctor"
    assert message["clinic_id"] == "clinic-notes-a"
    assert message["patient_id"] == patient["id"]

    listed = client.get(
        f"/v1/dental/doctor/patients/{patient['id']}/messages"
    )
    assert listed.status_code == 200
    assert listed.json()["messages"] == [message]

    denied = client.get(
        f"/v1/dental/doctor/patients/{other_patient['id']}/messages"
    )
    assert denied.status_code == 404

    audit = store._execute(
        "SELECT action,metadata_json FROM dental_audit_events "
        "WHERE resource_type='patient_message'"
    )
    assert audit and audit[0]["action"] == "patient_message.sent"
    assert "Please confirm" not in audit[0]["metadata_json"]
