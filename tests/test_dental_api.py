from fastapi.testclient import TestClient

from universal_intelligence_engine.dental_api import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)


def test_dental_health_is_public(monkeypatch):
    monkeypatch.setenv("DENTAL_API_KEY", "test-key")
    client = TestClient(app)
    response = client.get("/v1/dental/health")
    assert response.status_code == 200
    assert response.json()["service"] == "dental-api"


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
            "note": None,
            "intelligence_request": false,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requested"
    assert body["appointment"]["status"] == "requested"
    assert body["appointment"]["treatment_type"] == "Dental Check-up"


def test_scano_screening_integration_requires_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "dental.sqlite3"))
    monkeypatch.setenv("SCANO_INTEGRATION_KEY", "scano-test-key")
    from universal_intelligence_engine import dental_api

    dental_api._store = None
    client = TestClient(app)
    payload = {
        "external_id": "scan-001",
        "screening_id": "screening-001",
        "risk_score": 0.72,
        "findings": [{"condition": "calculus", "confidence": 0.91}],
        "evidence": [{"type": "image", "id": "img-001"}],
        "raw_result": {"provider": "scanO", "version": "sandbox"},
    }
    denied = client.post("/v1/dental/integrations/scano/screening", json=payload)
    assert denied.status_code == 401

    accepted = client.post(
        "/v1/dental/integrations/scano/screening",
        headers={"Authorization": "Bearer scano-test-key"},
        json=payload,
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["screening"]["source"] == "scanO"
    assert body["screening"]["external_id"] == "scan-001"
    assert body["screening"]["findings"][0]["condition"] == "calculus"
