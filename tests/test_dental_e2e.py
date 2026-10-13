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
    appointment_response = client.post(
        "/v1/dental/public/appointments",
        json={
            "name": "E2E Patient",
            "phone": "9876543210",
            "starts_at": "2026-10-10T10:00:00+05:30",
            "treatment_type": "Dental consultation",
        },
    )
    assert appointment_response.status_code == 200
    appointment = appointment_response.json()["appointment"]
    patient_id = appointment["patient_id"]

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-a", "clinic_id": "clinic-a", "role": "doctor"
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
        json={"patient_id": patient_id, "tooth_fdi": "46", "status": "attention", "note": "private clinical note"},
    )
    assert chart.status_code == 200

    # Patient AI, consent, linking, history, and AI-prefixed patient routes are
    # disabled at the API boundary; doctor-facing clinical AI remains separate.
    for method, path, payload in [
        ("POST", "/v1/dental/ai/patient/chat", {"message": "hello"}),
        ("POST", "/v1/dental/ai/patient/consent", {"consent": True}),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "ABCDEFGH"}),
        ("GET", "/v1/dental/ai/patient/appointments", None),
        ("GET", "/v1/dental/ai/patient/chart", None),
        ("DELETE", "/v1/dental/ai/patient/history", None),
    ]:
        response = client.request(method, path, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"

    app.dependency_overrides[dental_api.require_doctor] = lambda: {
        "id": "doctor-b", "clinic_id": "clinic-b", "role": "doctor"
    }
    denied = client.post(
        "/v1/dental/ai/doctor/patient-summary",
        json={"patient_id": patient_id},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"
