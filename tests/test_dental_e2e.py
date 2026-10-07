import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app
from universal_intelligence_engine import dental_api, dental_ai


@pytest.fixture()
def e2e_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "e2e.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")
    dental_api._store = None
    yield
    app.dependency_overrides.clear()
    dental_api._store = None


def test_patient_books_doctor_updates_patient_sees_ai_and_cross_clinic_denied(e2e_db, monkeypatch):
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

    app.dependency_overrides[dental_api.require_doctor] = lambda: {"id": "doctor-a", "clinic_id": "clinic-a", "role": "doctor"}
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

    store = dental_api.get_dental_store()
    store.link_patient_user(patient_id, "patient-user")

    app.dependency_overrides[dental_ai.require_patient] = lambda: {"user_id": "patient-user", "email": "patient@example.com"}
    patient_appointments = client.get("/v1/dental/ai/patient/appointments")
    patient_chart = client.get("/v1/dental/ai/patient/chart")
    assert patient_appointments.json()["result"][0]["status"] == "confirmed"
    assert patient_chart.json()["result"] == [{"tooth_fdi": "46", "status": "attention"}]

    store.set_ai_consent("patient-user", True)

    async def fake_uie(capability, goal, context, disclaimer=dental_ai.DISCLAIMER_PATIENT):
        return {
            "result": "Real backend contract result from the AI endpoint.",
            "confidence": 0.91,
            "sources": [{"title": "Test evidence", "url": "https://example.test/source"}],
            "disclaimers": [disclaimer],
            "request_id": "e2e-ai-request",
            "capability": capability,
        }

    monkeypatch.setattr(dental_ai, "_uie", fake_uie)
    ai = client.post("/v1/dental/ai/patient/chat", json={"message": "Explain my dental visit."})
    assert ai.status_code == 200
    assert ai.json()["result"] == "Real backend contract result from the AI endpoint."
    assert ai.json()["request_id"] == "e2e-ai-request"

    app.dependency_overrides[dental_api.require_doctor] = lambda: {"id": "doctor-b", "clinic_id": "clinic-b", "role": "doctor"}
    denied = client.post(
        "/v1/dental/ai/doctor/patient-summary",
        json={"patient_id": patient_id},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"
