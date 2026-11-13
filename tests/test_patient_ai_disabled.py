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

DISABLED_PATIENT_METHODS = [
    ("POST", "/v1/dental/ai/patient/chat"),
    ("POST", "/v1/dental/ai/patient/triage"),
    ("POST", "/v1/dental/ai/patient/treatment-explanation"),
    ("POST", "/v1/dental/ai/patient/aftercare"),
    ("POST", "/v1/dental/ai/patient/consent"),
    ("GET", "/v1/dental/ai/patient/history"),
    ("DELETE", "/v1/dental/ai/patient/history"),
    ("POST", "/v1/dental/ai/patient/link"),
    ("GET", "/v1/dental/ai/patient/profile"),
    ("GET", "/v1/dental/ai/patient/appointments"),
    ("GET", "/v1/dental/ai/patient/chart"),
    ("POST", "/v1/dental/ai/patient/device"),
    ("POST", "/v1/dental/ai/patient/recall"),
    ("GET", "/v1/dental/patient/linked-records"),
    ("POST", "/v1/dental/patient/link"),
    ("GET", "/v1/dental/patient/appointments"),
    ("POST", "/v1/dental/patient/appointments"),
    ("GET", "/v1/dental/patient/chart"),
    ("GET", "/v1/dental/patient/treatment-plans"),
    ("GET", "/v1/dental/patient/invoices"),
    ("GET", "/v1/dental/patient/receipts"),
    ("GET", "/v1/dental/patient/clinical-notes"),
    ("GET", "/v1/dental/patient/messages"),
    ("POST", "/v1/dental/patient/messages"),
]


def test_patient_ai_and_account_routes_are_server_disabled():
    for method, path in DISABLED_PATIENT_METHODS:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_FREE"


def test_openapi_hides_all_patient_account_and_ai_routes_but_keeps_doctor_routes():
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(
        path.startswith(("/v1/dental/ai/patient/", "/v1/dental/patient/"))
        for path in paths
    )
    assert "/v1/dental/doctor/patients/{patient_id}/invite" in paths
    assert "/v1/dental/ai/doctor/patient-summary" in paths
    assert "/v1/dental/intelligence/run" in paths
    assert "/v1/dental/public/appointments" in paths


def test_patient_routes_normalize_trailing_slashes():
    routes = [
        ("POST", "/v1/dental/ai/patient/consent/"),
        ("GET", "/v1/dental/ai/patient/history/"),
        ("DELETE", "/v1/dental/ai/patient/history/"),
        ("GET", "/v1/dental/patient/linked-records/"),
        ("GET", "/v1/dental/patient/invoices/"),
    ]
    for method, path in routes:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_FREE"
