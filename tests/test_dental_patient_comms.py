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
    store._execute("UPDATE dental_patients SET user_id=? WHERE id=?", ("supabase-user-a", patient_a["id"]))
    store._execute("UPDATE dental_patients SET user_id=? WHERE id=?", ("supabase-user-b", patient_b["id"]))

    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    patient_actor = {"user_id": "supabase-user-a"}
    monkeypatch.setattr(dental_patient_comms, "get_dental_store", lambda: store)

    app = FastAPI()
    app.include_router(dental_patient_comms.router)
    app.dependency_overrides[dental_patient_comms.require_doctor] = lambda: actor
    app.dependency_overrides[dental_patient_comms.require_patient] = lambda: patient_actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b


def test_clinical_notes_are_private_until_doctor_explicitly_shares(comms_api):
    client, _store, actor, _patient_actor, patient_a, _patient_b = comms_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/clinical-notes",
        json={"title": "Examination", "content": "Synthetic clinical note."},
    )
    assert created.status_code == 200, created.text
    note = created.json()["result"]
    assert note["patient_visible"] is False
    assert client.get("/v1/dental/patient/clinical-notes").json()["result"] == []

    shared = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"patient_visible": True, "expected_version": 1},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["result"]["version"] == 2
    visible = client.get("/v1/dental/patient/clinical-notes").json()["result"]
    assert len(visible) == 1
    assert visible[0]["content"] == "Synthetic clinical note."
    assert "clinic_id" not in visible[0]
    assert "patient_visible" not in visible[0]

    stale = client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"content": "Stale edit", "expected_version": 1},
    )
    assert stale.status_code == 409

    actor["clinic_id"] = "clinic-b"
    assert client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/clinical-notes").status_code == 404
    assert client.patch(
        f"/v1/dental/doctor/clinical-notes/{note['id']}",
        json={"patient_visible": False, "expected_version": 2},
    ).status_code == 404


def test_patient_and_doctor_messages_are_tenant_scoped_and_shared(comms_api):
    client, _store, actor, patient_actor, patient_a, patient_b = comms_api
    doctor_send = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/messages",
        json={"body": "Your appointment request has been received."},
    )
    assert doctor_send.status_code == 200, doctor_send.text
    assert doctor_send.json()["result"]["sender_type"] == "doctor"

    patient_read = client.get(f"/v1/dental/patient/messages?patient_id={patient_a['id']}")
    assert patient_read.status_code == 200
    assert patient_read.json()["result"][0]["body"] == "Your appointment request has been received."

    patient_send = client.post(
        f"/v1/dental/patient/messages?patient_id={patient_a['id']}",
        json={"body": "Thank you. I prefer an afternoon visit."},
    )
    assert patient_send.status_code == 200, patient_send.text
    assert patient_send.json()["result"]["sender_type"] == "patient"

    doctor_read = client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/messages")
    assert doctor_read.status_code == 200
    assert [item["sender_type"] for item in doctor_read.json()["result"]] == ["doctor", "patient"]

    wrong_patient = client.get(f"/v1/dental/patient/messages?patient_id={patient_b['id']}")
    assert wrong_patient.status_code == 403

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
    assert client.get("/v1/dental/patient/clinical-notes").status_code == 401
    assert client.get("/v1/dental/doctor/patients/missing/messages").status_code == 401
    assert client.get("/v1/dental/patient/messages").status_code == 401


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
