from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app

client = TestClient(app)

DISABLED_AI_PATHS = [
    "/v1/dental/ai/patient/chat",
    "/v1/dental/ai/patient/triage",
    "/v1/dental/ai/patient/treatment-explanation",
    "/v1/dental/ai/patient/aftercare",
    "/v1/dental/ai/patient/consent",
    "/v1/dental/ai/patient/history",
]

DISABLED_AI_METHODS = [
    ("POST", "/v1/dental/ai/patient/chat"),
    ("POST", "/v1/dental/ai/patient/triage"),
    ("POST", "/v1/dental/ai/patient/treatment-explanation"),
    ("POST", "/v1/dental/ai/patient/aftercare"),
    ("POST", "/v1/dental/ai/patient/consent"),
    ("GET", "/v1/dental/ai/patient/history"),
    ("DELETE", "/v1/dental/ai/patient/history"),
]


def test_patient_ai_routes_are_server_disabled_for_every_method():
    for method, path in DISABLED_AI_METHODS:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"


def test_patient_account_routes_are_disabled_without_supabase_auth():
    routes = [
        ("POST", "/v1/dental/ai/patient/link"),
        ("GET", "/v1/dental/ai/patient/appointments"),
        ("GET", "/v1/dental/ai/patient/chart"),
        ("POST", "/v1/dental/ai/patient/device"),
        ("POST", "/v1/dental/ai/patient/recall"),
        ("GET", "/v1/dental/patient/treatment-plans"),
        ("GET", "/v1/dental/patient/invoices"),
        ("GET", "/v1/dental/patient/receipts"),
        ("GET", "/v1/dental/patient/documents"),
        ("POST", "/v1/dental/patient/documents/document-1/download-intent"),
        ("POST", "/v1/dental/patient/link"),
        ("GET", "/v1/dental/patient/appointments"),
        ("POST", "/v1/dental/patient/appointments"),
        ("GET", "/v1/dental/patient/chart"),
    ]
    for method, path in routes:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 403, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNTS_DISABLED"


def test_openapi_hides_patient_account_routes_and_keeps_doctor_ai_and_public_booking():
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(
        path.startswith(("/v1/dental/patient/", "/v1/dental/ai/patient/"))
        for path in paths
    )
    assert "/v1/dental/ai/doctor/patient-summary" in paths
    assert "/v1/dental/intelligence/run" in paths
    assert "/v1/dental/public/appointments" in paths


def test_patient_ai_disabled_routes_normalize_trailing_slashes():
    routes = [
        ("POST", "/v1/dental/ai/patient/consent/"),
        ("GET", "/v1/dental/ai/patient/history/"),
        ("DELETE", "/v1/dental/ai/patient/history/"),
    ]
    for method, path in routes:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"
