from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "expected_code"),
    [
        ("GET", "/v1/dental/patient/linked-records", "PATIENT_ACCOUNTS_DISABLED"),
        ("POST", "/v1/dental/patient/link", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/patient/appointments", "PATIENT_ACCOUNTS_DISABLED"),
        ("POST", "/v1/dental/patient/appointments", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/patient/chart", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/patient/treatment-plans", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/patient/invoices", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/patient/receipts", "PATIENT_ACCOUNTS_DISABLED"),
        ("GET", "/v1/dental/ai/patient/profile", "PATIENT_AI_DISABLED"),
        ("GET", "/v1/dental/ai/patient/history", "PATIENT_AI_DISABLED"),
        ("DELETE", "/v1/dental/ai/patient/history", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/chat", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/triage", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/treatment-explanation", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/aftercare", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/device", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/ai/patient/recall", "PATIENT_AI_DISABLED"),
        ("POST", "/v1/dental/doctor/patients/synthetic-patient/invite", "PATIENT_INVITES_DISABLED"),
    ],
)
def test_legacy_patient_account_and_invite_routes_are_disabled(
    client: TestClient, method: str, path: str, expected_code: str
):
    response = client.request(method, path, json={})
    expected_status = 404 if expected_code == "PATIENT_AI_DISABLED" else 410
    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]["code"] == expected_code
    assert response.headers["cache-control"] == "no-store, private"


def test_public_guest_booking_remains_account_free_and_unblocked(client: TestClient):
    # Invalid empty input exercises request validation only; no appointment is created.
    response = client.post("/v1/dental/public/appointments", json={})
    assert response.status_code == 422
    assert response.status_code not in {401, 403, 410}


def test_disabled_patient_routes_are_not_advertised_in_openapi():
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/v1/dental/patient/") for path in paths)
    assert not any(path.startswith("/v1/dental/ai/patient/") for path in paths)
    assert not any(
        path.startswith("/v1/dental/doctor/patients/") and path.endswith("/invite")
        for path in paths
    )
    assert "/v1/dental/public/appointments" in paths
