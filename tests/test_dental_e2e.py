from datetime import UTC, datetime, timedelta
import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.api import app


@pytest.fixture()
def e2e_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "e2e.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")
    dental_api._store = None
    yield
    app.dependency_overrides.clear()
    dental_api._store = None


def test_patient_books_doctor_updates_and_patient_ai_is_disabled(e2e_db):
    client = TestClient(app)
    starts_at = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    appointment_response = client.post(
        "/v1/dental/public/appointments",
        json={
            "name": "E2E Patient",
            "phone": "9876543210",
            "starts_at": starts_at,
            "treatment_type": "Dental consultation",
        },
    )
    assert appointment_response.status_code == 200
    appointment = appointment_response.json()["appointment"]
    patient_id = appointment["patient_id"]

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-a",
        "clinic_id": "clinic-a",
        "role": "doctor",
    }
    seen = client.get("/v1/dental/appointments")
    assert seen.status_code == 200
    assert any(row["id"] == appointment["id"] for row in seen.json())

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
    for method, path, payload in [
        ("POST", "/v1/dental/ai/patient/chat", {"message": "hello"}),
        ("POST", "/v1/dental/ai/patient/triage", {"symptoms": "toothache"}),
        ("POST", "/v1/dental/ai/patient/consent", {"consent": True}),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "synthetic-invite"}),
        ("GET", "/v1/dental/ai/patient/appointments", None),
        ("GET", "/v1/dental/ai/patient/chart", None),
        ("DELETE", "/v1/dental/ai/patient/history", None),
        ("GET", "/v1/dental/patient/linked-records", None),
        ("POST", "/v1/dental/patient/link", {"invite_code": "synthetic-invite"}),
        ("GET", "/v1/dental/patient/appointments", None),
        ("GET", "/v1/dental/patient/chart", None),
        ("GET", "/v1/dental/patient/treatment-plans", None),
        ("GET", "/v1/dental/patient/invoices", None),
        ("GET", "/v1/dental/patient/receipts", None),
        ("GET", "/v1/dental/patient/clinical-notes", None),
        ("GET", "/v1/dental/patient/messages", None),
        ("POST", "/v1/dental/patient/messages", {"body": "synthetic"}),
        ("GET", "/v1/dental/patient/documents", None),
        ("POST", "/v1/dental/patient/documents/document-1/download-intent", {}),
    ]:
        response = client.request(method, path, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_FREE"

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
