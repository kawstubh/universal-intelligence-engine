from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app

client = TestClient(app)

DISABLED_AI_PATHS = [
    "/v1/dental/ai/patient/chat",
    "/v1/dental/ai/patient/triage",
    "/v1/dental/ai/patient/treatment-explanation",
    "/v1/dental/ai/patient/aftercare",
]


def test_patient_ai_routes_are_server_disabled():
    for path in DISABLED_AI_PATHS:
        response = client.post(path, json={})
        assert response.status_code == 404, (path, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"


def test_patient_account_data_routes_remain_available_behind_patient_auth():
    for path in [
        "/v1/dental/ai/patient/link",
        "/v1/dental/ai/patient/consent",
        "/v1/dental/ai/patient/history",
        "/v1/dental/ai/patient/appointments",
        "/v1/dental/ai/patient/chart",
    ]:
        response = client.get(path) if path.endswith(("history", "appointments", "chart")) else client.post(path, json={})
        assert response.status_code == 401, (path, response.status_code, response.text)


def test_openapi_hides_patient_ai_and_keeps_doctor_ai_and_patient_records():
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path in paths for path in DISABLED_AI_PATHS)
    assert "/v1/dental/ai/patient/appointments" in paths
    assert "/v1/dental/ai/patient/chart" in paths
    assert "/v1/dental/ai/doctor/patient-summary" in paths
    assert "/v1/dental/intelligence/run" in paths
