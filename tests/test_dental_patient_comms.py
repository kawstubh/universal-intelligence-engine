from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_patient_comms
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def comms_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "clinical-comms.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    store.create_organisation({"id": "org-a", "name": "Clinic Group", "organisation_type": "group"})
    store.create_organisation({"id": "org-b", "name": "Other Clinic", "organisation_type": "clinic"})
    store.create_clinic({"id": "clinic-a", "organisation_id": "org-a", "name": "Main Clinic", "clinic_type": "clinic"})
    store.create_clinic({"id": "clinic-b", "organisation_id": "org-b", "name": "Other Clinic", "clinic_type": "clinic"})
    patient_a = store.create_patient({"name": "Synthetic Patient A", "phone": "9000000001", "age": "35"}, "clinic-a")
    patient_b = store.create_patient({"name": "Synthetic Patient B", "phone": "9000000002", "age": "29"}, "clinic-b")
    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    patient_actor = {}
    monkeypatch.setattr(dental_patient_comms, "get_dental_store", lambda: store)

    app = FastAPI()
    app.include_router(dental_patient_comms.router)
    app.dependency_overrides[dental_patient_comms.require_doctor] = lambda: actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b


def test_clinical_notes_remain_clinic_only(comms_api):
    client, _store, actor, _patient_actor, patient_a, _patient_b = comms_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/clinical-notes",
        json={"title": "Examination", "content": "Synthetic clinical note."},
    )
    assert created.status_code == 200, created.text
    note = created.json()["result"]
    assert note["patient_visible"] is False
    assert client.get("/v1/dental/patient/clinical-notes").status_code == 404

    updated = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"content": "Updated synthetic clinic-only note.", "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["version"] == 2
    assert updated.json()["result"]["patient_visible"] is False
    assert client.get("/v1/dental/patient/clinical-notes").status_code == 404

    attempted_share = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"patient_visible": True, "expected_version": 2},
    )
    assert attempted_share.status_code == 422

    stale = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"content": "Stale edit", "expected_version": 1},
    )
    assert stale.status_code == 409

    actor["clinic_id"] = "clinic-b"
    assert client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/clinical-notes").status_code == 404
    assert client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"content": "Cross-clinic edit", "expected_version": 2},
    ).status_code == 404


def test_doctor_contact_messages_are_tenant_scoped_and_patient_routes_absent(comms_api):
    client, _store, actor, _patient_actor, patient_a, patient_b = comms_api
    doctor_send = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/messages",
        json={"body": "Your public appointment request has been received."},
    )
    assert doctor_send.status_code == 200, doctor_send.text
    assert doctor_send.json()["result"]["sender_type"] == "doctor"

    doctor_read = client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/messages")
    assert doctor_read.status_code == 200
    assert [item["sender_type"] for item in doctor_read.json()["result"]] == ["doctor"]

    assert client.get(f"/v1/dental/patient/messages?patient_id={patient_a['id']}").status_code == 404
    assert client.post(
        f"/v1/dental/patient/messages?patient_id={patient_a['id']}",
        json={"body": "Patient account messages are not supported."},
    ).status_code == 404

    actor["clinic_id"] = "clinic-b"
    assert client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/messages").status_code == 404
    assert client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/messages",
        json={"body": "Cross-tenant message"},
    ).status_code == 404


def test_messages_and_notes_require_authenticated_roles():
    app = FastAPI()
    app.include_router(dental_patient_comms.router)
    client = TestClient(app)
    assert client.get("/v1/dental/doctor/patients/missing/clinical-notes").status_code == 401
    assert client.get("/v1/dental/patient/clinical-notes").status_code == 404
    assert client.get("/v1/dental/doctor/patients/missing/messages").status_code == 401
    assert client.get("/v1/dental/patient/messages").status_code == 404


def test_clinical_note_validation_and_empty_message_validation(comms_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = comms_api
    invalid_note = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/clinical-notes",
        json={"title": "x", "content": ""},
    )
    assert invalid_note.status_code == 422
    empty_message = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/messages",
        json={"body": "   "},
    )
    assert empty_message.status_code == 400
