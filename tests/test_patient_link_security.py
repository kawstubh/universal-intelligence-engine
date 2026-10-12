from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app


def test_patient_ai_routes_are_disabled_server_side():
    """Patient-facing AI/data routes must remain inaccessible at launch."""
    client = TestClient(app)
    cases = [
        ("get", "/v1/dental/ai/patient/appointments", None),
        ("get", "/v1/dental/ai/patient/chart", None),
        ("post", "/v1/dental/ai/patient/link", {"phone": "9000000001"}),
        ("post", "/v1/dental/ai/patient/link", {"invite_code": "test-invite"}),
    ]
    for method, path, payload in cases:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
        assert response.status_code == 404, f"{method.upper()} {path} should be disabled, got {response.status_code}"
