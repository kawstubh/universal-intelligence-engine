from fastapi.testclient import TestClient

from universal_intelligence_engine.dental_api import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)


def test_dental_api_requires_auth(monkeypatch):
    monkeypatch.setenv("DENTAL_API_KEY", "test-key")
    client = TestClient(app)
    response = client.get("/v1/dental/health")
    assert response.status_code == 401


def test_chart_tooth_validation(monkeypatch):
    monkeypatch.setenv("DENTAL_API_KEY", "test-key")
    client = TestClient(app)
    response = client.post(
        "/v1/dental/patients/p1/chart",
        headers={"Authorization": "Bearer test-key"},
        json={"patient_id":"p1","tooth_fdi":"99","status":"Cavity","note":""},
    )
    assert response.status_code == 422

def test_public_appointment_intake_does_not_require_doctor_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "dental.sqlite3"))
    from universal_intelligence_engine import dental_api

    dental_api._store = None
    client = TestClient(app)
    response = client.post(
        "/v1/dental/public/appointments",
        json={
            "name": "Test Patient",
            "phone": "9876543210",
            "starts_at": "2026-10-06 10:00 AM",
            "treatment_type": "Dental Check-up",
            "note": "New patient",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requested"
    assert body["appointment"]["status"] == "requested"
    assert body["appointment"]["treatment_type"] == "Dental Check-up"
