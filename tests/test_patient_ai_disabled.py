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

PATIENT_ACCOUNT_METHODS = [
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
    ("POST", "/v1/dental/doctor/patients/patient-1/invite"),
]


def test_patient_ai_routes_are_server_disabled_for_every_method():
    for method, path in DISABLED_AI_METHODS:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"


def test_patient_account_data_routes_are_disabled_by_default(monkeypatch):
    monkeypatch.setenv("DENTAL_PATIENT_ACCOUNT_PORTAL_ENABLED", "false")
    for method, path in PATIENT_ACCOUNT_METHODS:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_PORTAL_DISABLED"


def test_openapi_hides_patient_ai_and_legacy_patient_account_routes(monkeypatch):
    monkeypatch.setenv("DENTAL_PATIENT_ACCOUNT_PORTAL_ENABLED", "false")
    app.openapi_schema = None
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path in paths for path in DISABLED_AI_PATHS)
    assert not any(
        path in paths
        for path in [
            "/v1/dental/ai/patient/profile",
            "/v1/dental/ai/patient/link",
            "/v1/dental/ai/patient/appointments",
            "/v1/dental/ai/patient/chart",
            "/v1/dental/patient/treatment-plans",
            "/v1/dental/patient/invoices",
            "/v1/dental/patient/receipts",
            "/v1/dental/patient/documents",
            "/v1/dental/patient/documents/{document_id}/download-intent",
            "/v1/dental/patient/link",
            "/v1/dental/patient/appointments",
            "/v1/dental/patient/chart",
            "/v1/dental/doctor/patients/{patient_id}/invite",
        ]
    )
    assert "/v1/dental/ai/doctor/patient-summary" in paths
    assert "/v1/dental/intelligence/run" in paths


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
