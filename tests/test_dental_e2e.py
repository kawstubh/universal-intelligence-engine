from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.api import app


@pytest.fixture()
def e2e_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "e2e.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")
    dental_api._store = None
    dental_store._store = None
    yield
    app.dependency_overrides.clear()
    dental_api._store = None
    dental_store._store = None


def test_public_guest_booking_doctor_workflow_and_patient_apis_disabled(e2e_db):
    client = TestClient(app)
    starts_at = (
        datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=7)
    ).replace(hour=10, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    appointment_response = client.post(
        "/v1/dental/public/appointments",
        json={
            "idempotency_key": "e2e-idempotency-key-000000000000",
            "name": "E2E Patient",
            "phone": "9876543210",
            "starts_at": starts_at,
            "treatment_type": "Dental check-up",
            "intelligence_request": False,
        },
    )
    assert appointment_response.status_code == 200
    public_receipt = appointment_response.json()
    assert public_receipt["status"] == "requested"
    assert public_receipt["reference"].startswith("ORL-")
    assert "patient_id" not in public_receipt
    assert "appointment_id" not in public_receipt
    assert "appointment" not in public_receipt

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-a",
        "clinic_id": "clinic-a",
        "role": "doctor",
    }
    seen = client.get("/v1/dental/appointments")
    assert seen.status_code == 200
    appointment = next(
        row for row in seen.json()
        if row["patient_name"] == "E2E Patient" and row["starts_at"] == starts_at
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

    # The public journey is account-free; patient account and record APIs are disabled.
    for method, path, payload in [
        ("POST", "/v1/dental/ai/patient/chat", {"message": "hello"}),
        ("POST", "/v1/dental/ai/patient/triage", {"symptoms": "toothache"}),
        ("POST", "/v1/dental/ai/patient/consent", {"consent": True}),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "ABCDEFGH"}),
        ("GET", "/v1/dental/ai/patient/appointments", None),
        ("GET", "/v1/dental/ai/patient/chart", None),
        ("DELETE", "/v1/dental/ai/patient/history", None),
    ]:
        response = client.request(method, path, json=payload)
        if path.endswith(("/chat", "/triage", "/treatment-explanation", "/aftercare", "/consent", "/history")):
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"
        else:
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"]["code"] == "PATIENT_ACCOUNTS_DISABLED"

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-b",
        "clinic_id": "clinic-b",
        "role": "doctor",
    }
    denied = client.post(
        "/v1/dental/ai/doctor/patient-summary",
        json={"patient_id": patient_id},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"

