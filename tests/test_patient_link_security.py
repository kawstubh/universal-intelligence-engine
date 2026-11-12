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
        assert response.status_code == 403, (
            f"{method.upper()} {path} must remain disabled, got {response.status_code}"
        )
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNTS_DISABLED"



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
    monkeypatch.setitem(
        app.dependency_overrides,
        require_patient,
        lambda: {"user_id": "supabase-user-123", "email": "synthetic@example.invalid"},
    )
    response = TestClient(app).get("/v1/dental/ai/patient/profile")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["linked"] is True
    assert result["patient"] == {
        "id": "patient-123",
        "name": "Synthetic Patient",
        "clinic_id": "clinic-123",
    }


def test_patient_profile_reports_unlinked_account_without_patient_data(monkeypatch):
    class FakeStore:
        def patient_by_user_id(self, user_id):
            assert user_id == "supabase-user-456"
            return None

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: FakeStore())
    monkeypatch.setitem(
        app.dependency_overrides,
        require_patient,
        lambda: {"user_id": "supabase-user-456", "email": "unlinked@example.invalid"},
    )
    response = TestClient(app).get("/v1/dental/ai/patient/profile")

    assert response.status_code == 200
    assert response.json()["result"] == {"linked": False, "patient": None}


def test_patient_appointments_expose_only_patient_safe_fields(monkeypatch):
    class FakeStore:
        def patient_by_user_id(self, user_id):
            assert user_id == "supabase-user-789"
            return {"id": "patient-789", "clinic_id": "clinic-789"}

        def appointments(self, clinic_id, patient_id):
            assert clinic_id == "clinic-789"
            assert patient_id == "patient-789"
            return [{
                "id": "appointment-1",
                "patient_id": patient_id,
                "clinic_id": clinic_id,
                "starts_at": "2026-12-01T10:00:00+05:30",
                "treatment_type": "Dental consultation",
                "status": "confirmed",
                "note": "private internal note",
            }]

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: FakeStore())
    monkeypatch.setitem(
        app.dependency_overrides,
        require_patient,
        lambda: {"user_id": "supabase-user-789", "email": "patient@example.invalid"},
    )
    response = TestClient(app).get("/v1/dental/ai/patient/appointments")

    assert response.status_code == 200
    assert response.json()["result"] == [{
        "id": "appointment-1",
        "starts_at": "2026-12-01T10:00:00+05:30",
        "treatment_type": "Dental consultation",
        "status": "confirmed",
    }]


def test_patient_chart_exposes_only_shared_chart_fields(monkeypatch):
    class FakeStore:
        def patient_by_user_id(self, user_id):
            assert user_id == "supabase-user-321"
            return {"id": "patient-321", "clinic_id": "clinic-321"}

        def chart(self, patient_id, clinic_id):
            assert patient_id == "patient-321"
            assert clinic_id == "clinic-321"
            return [{
                "id": "chart-1",
                "patient_id": patient_id,
                "clinic_id": clinic_id,
                "tooth_fdi": "11",
                "status": "filled",
                "note": "private clinical note",
            }]

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: FakeStore())
    monkeypatch.setitem(
        app.dependency_overrides,
        require_patient,
        lambda: {"user_id": "supabase-user-321", "email": "patient@example.invalid"},
    )
    response = TestClient(app).get("/v1/dental/ai/patient/chart")

    assert response.status_code == 200
    assert response.json()["result"] == [{"tooth_fdi": "11", "status": "filled"}]
