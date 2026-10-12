from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app

client = TestClient(app)


def test_patient_ai_routes_are_server_disabled():
    cases = [
        ("POST", "/v1/dental/ai/patient/chat", {"message": "hello"}),
        ("POST", "/v1/dental/ai/patient/consent", {"consent": True}),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "ABCDEFGH"}),
        ("POST", "/v1/dental/ai/patient/triage", {"symptoms": "toothache"}),
        ("DELETE", "/v1/dental/ai/patient/history", None),
        ("POST", "/v1/dental/ai/patient/device", {"expo_push_token": "ExponentPushToken[not-a-real-token]"}),
    ]
    for method, path, payload in cases:
        response = client.request(method, path, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"


def test_openapi_hides_patient_ai_and_keeps_doctor_ai():
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert not any(path.startswith("/v1/dental/ai/patient") for path in paths)
    assert "/v1/dental/ai/doctor/patient-summary" in paths
    assert "/v1/dental/intelligence/run" in paths


def test_uie_run_requires_server_side_bearer_key(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "test-only-key")
    response = client.post("/v1/intelligence/run", json={"goal": "test"})
    assert response.status_code == 401
    response = client.post(
        "/v1/intelligence/run",
        headers={"Authorization": "Bearer test-only-key"},
        json={"goal": "test"},
    )
    # Auth should pass; provider setup may intentionally fail closed in test env.
    assert response.status_code != 401
