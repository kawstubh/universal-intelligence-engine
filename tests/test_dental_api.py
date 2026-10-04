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
