from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.api import app
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture()
def e2e_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "e2e.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")
    monkeypatch.setenv("DENTAL_PUBLIC_BOOKING_HASH_KEY", "synthetic-public-booking-hash-key-32-bytes-minimum")
    monkeypatch.setattr(dental_store, "_store", None)
    store = DentalStore()
    store.create_organisation(
        {"id": "org-e2e", "name": "Synthetic E2E Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-a",
            "organisation_id": "org-e2e",
            "name": "Synthetic E2E Clinic",
            "clinic_type": "clinic",
        }
    )
    yield store
    app.dependency_overrides.clear()
    monkeypatch.setattr(dental_store, "_store", None)


def test_patient_books_doctor_updates_and_patient_account_routes_are_disabled(e2e_db):
    client = TestClient(app)
    starts_at = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    appointment_response = client.post(
        "/v1/dental/public/appointments",
        json={
            "idempotency_key": "oralyn-e2e-guest-booking-0001",
            "name": "E2E Synthetic Patient",
            "phone": "9876543210",
            "starts_at": starts_at,
            "treatment_type": "Dental Check-up & Consultation",
            "intelligence_request": False,
        },
    )
    assert appointment_response.status_code == 200, appointment_response.text
    public_result = appointment_response.json()
    assert public_result["status"] == "requested"
    assert public_result["reference"].startswith("ORL-")
    assert "patient_id" not in public_result
    assert "appointment" not in public_result

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-a",
        "clinic_id": "clinic-a",
        "role": "doctor",
    }
    seen = client.get("/v1/dental/appointments")
    assert seen.status_code == 200
    appointment = next(
        row for row in seen.json()
        if row["treatment_type"] == "Dental Check-up & Consultation"
    )
    patient_id = appointment["patient_id"]

    updated = client.patch(
        f"/v1/dental/appointments/{appointment['id']}",
        json={"status": "confirmed"},
    )
    assert updated.status_code == 200
    chart = client.post(
        f"/v1/dental/patients/{patient_id}/chart",
        json={
            "patient_id": patient_id,
            "tooth_fdi": "46",
            "status": "attention",
            "note": "private clinical note",
        },
    )
    assert chart.status_code == 200

    # No patient account, Supabase login, linked-record or patient AI API is exposed.
    disabled_routes = [
        ("POST", "/v1/dental/ai/patient/chat", {"message": "hello"}),
        ("POST", "/v1/dental/ai/patient/triage", {"symptoms": "toothache"}),
        ("POST", "/v1/dental/ai/patient/consent", {"consent": True}),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "ABCDEFGH"}),
        ("GET", "/v1/dental/ai/patient/appointments", None),
        ("GET", "/v1/dental/ai/patient/chart", None),
        ("DELETE", "/v1/dental/ai/patient/history", None),
        ("GET", "/v1/dental/patient/linked-records", None),
        ("GET", "/v1/dental/patient/invoices", None),
        ("GET", "/v1/dental/patient/clinical-notes", None),
        ("POST", f"/v1/dental/doctor/patients/{patient_id}/invite", None),
        ("POST", f"/v1/dental/ai/doctor/patients/{patient_id}/invite", None),
    ]
    for method, path, payload in disabled_routes:
        response = client.request(method, path, json=payload)
        # Legacy account/link endpoints are not registered; standard FastAPI 404 is expected.
        assert response.status_code == 404, (method, path, response.status_code, response.text)

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-b",
        "clinic_id": "clinic-b",
        "role": "doctor",
    }
    denied = client.post(
        "/v1/dental/ai/doctor/patient-summary",
        json={"patient_id": patient_id, "goal": "Summarise the authorised record"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"
