from fastapi.testclient import TestClient

import universal_intelligence_engine.dental_ai as dental_ai
from universal_intelligence_engine.api import app


def test_legacy_patient_account_and_link_routes_are_disabled_without_supabase_auth(monkeypatch):
    def unexpected_supabase_call(_authorization):
        raise AssertionError("The account-free patient journey must not call Supabase patient auth")

    monkeypatch.setattr(dental_ai, "_supabase_user", unexpected_supabase_call)
    client = TestClient(app)
    cases = [
        ("GET", "/v1/dental/ai/patient/profile", None),
        ("GET", "/v1/dental/ai/patient/appointments", None),
        ("GET", "/v1/dental/ai/patient/chart", None),
        ("POST", "/v1/dental/ai/patient/link", {"invite_code": "synthetic-invite"}),
        ("GET", "/v1/dental/patient/linked-records", None),
        ("GET", "/v1/dental/patient/invoices", None),
        ("GET", "/v1/dental/patient/clinical-notes", None),
    ]
    for method, path, payload in cases:
        response = client.request(method, path, json=payload)
        assert response.status_code == 404, (
            f"{method} {path} must be disabled, got {response.status_code}"
        )
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_FREE"


def test_no_patient_account_or_linked_record_routes_are_published():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert not any(
        path.startswith(("/v1/dental/ai/patient/", "/v1/dental/patient/"))
        for path in paths
    )
