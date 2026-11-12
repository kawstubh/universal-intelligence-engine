from fastapi.testclient import TestClient

import universal_intelligence_engine.dental_ai as dental_ai
from universal_intelligence_engine.dental_ai import require_patient
from universal_intelligence_engine.api import app


def test_patient_account_routes_require_patient_authentication():
    """Patient account and records routes stay separate from doctor auth."""
    client = TestClient(app)
    cases = [
        ("get", "/v1/dental/ai/patient/profile", None),
        ("get", "/v1/dental/ai/patient/appointments", None),
        ("get", "/v1/dental/ai/patient/chart", None),
        ("post", "/v1/dental/ai/patient/link", {"phone": "9000000001"}),
        ("post", "/v1/dental/ai/patient/link", {"invite_code": "test-invite"}),
    ]
    for method, path, payload in cases:
        response = (
            getattr(client, method)(path, json=payload)
            if payload is not None
            else getattr(client, method)(path)
        )
        assert response.status_code == 401, (
            f"{method.upper()} {path} must require patient auth, got {response.status_code}"
        )



def test_patient_profile_returns_only_the_linked_patients_safe_fields(monkeypatch):
    class FakeStore:
        def patient_by_user_id(self, user_id):
            assert user_id == "supabase-user-123"
            return {
                "id": "patient-123",
                "name": "Synthetic Patient",
                "clinic_id": "clinic-123",
                "phone": "9000000000",
                "age": "32",
                "user_id": user_id,
            }

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: FakeStore())
    app.dependency_overrides[require_patient] = lambda: {
        "user_id": "supabase-user-123",
        "email": "synthetic@example.invalid",
    }
    try:
        response = TestClient(app).get("/v1/dental/ai/patient/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["linked"] is True
    assert result["patient"] == {
        "id": "patient-123",
        "name": "Synthetic Patient",
        "clinic_id": "clinic-123",
    }
    assert "phone" not in result["patient"]
    assert "age" not in result["patient"]
    assert "user_id" not in result["patient"]


def test_patient_profile_reports_unlinked_account_without_patient_data(monkeypatch):
    class FakeStore:
        def patient_by_user_id(self, user_id):
            assert user_id == "supabase-user-456"
            return None

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: FakeStore())
    app.dependency_overrides[require_patient] = lambda: {
        "user_id": "supabase-user-456",
        "email": "unlinked@example.invalid",
    }
    try:
        response = TestClient(app).get("/v1/dental/ai/patient/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["result"] == {"linked": False, "patient": None}
