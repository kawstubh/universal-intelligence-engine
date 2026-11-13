from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app

PUBLIC_PATHS = {
    "/v1/dental/health",
    "/v1/dental/auth/password/login",
    "/v1/dental/assistant/auth/login",
    "/v1/dental/public/appointments",
    "/v1/dental/billing/plans",
    "/v1/dental/billing/razorpay/webhook",
}
PATIENT_PATH_PREFIX = "/v1/dental/ai/patient/"
PATIENT_DATA_PATH_PREFIX = "/v1/dental/patient/"
ASSISTANT_PATH_PREFIX = "/v1/dental/assistant/"
DISABLED_PATIENT_AI = {
    "/v1/dental/ai/patient/chat",
    "/v1/dental/ai/patient/triage",
    "/v1/dental/ai/patient/treatment-explanation",
    "/v1/dental/ai/patient/aftercare",
    "/v1/dental/ai/patient/consent",
    "/v1/dental/ai/patient/history",
}


def _is_patient_ai_route(path: str) -> bool:
    # Only patient AI actions are disabled; public guest booking is account-free.
    return path.rstrip("/") in DISABLED_PATIENT_AI


def _sample_path(path: str) -> str:
    return (
        path.replace("{appointment_id}", "missing-appointment")
        .replace("{patient_id}", "missing-patient")
        .replace("{assistant_id}", "missing-assistant")
        .replace("{invoice_id}", "missing-invoice")
    )


def test_no_demo_or_otp_release_auth_symbols():
    import universal_intelligence_engine.dental_api as api
    import universal_intelligence_engine.dental_auth as auth

    assert not hasattr(auth, "DEMO_AUTH")
    assert not hasattr(auth, "DEMO_OTP")
    assert not hasattr(auth, "request_otp")
    assert not hasattr(auth, "verify_otp")
    assert not hasattr(auth, "login_google")
    assert not any(
        getattr(route, "path", "") == "/v1/dental/auth/google"
        for route in app.routes
    )
    assert "otp/request" not in api.__dict__.get("router").routes.__repr__().lower()


def test_protected_dental_routes_require_the_correct_auth_dependency():
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1/dental/"):
            continue
        if path in PUBLIC_PATHS or _is_patient_ai_route(path):
            continue
        dependency_calls = {
            getattr(dep.call, "__name__", "")
            for dep in getattr(route, "dependant", None).dependencies
        }
        if path.startswith(ASSISTANT_PATH_PREFIX):
            assert "require_assistant" in dependency_calls, f"missing assistant auth on {path}"
        elif path.startswith((PATIENT_PATH_PREFIX, PATIENT_DATA_PATH_PREFIX)):
            assert "require_patient" in dependency_calls, f"missing patient auth on {path}"
        else:
            assert "require_doctor" in dependency_calls, f"missing doctor auth on {path}"


def test_protected_dental_requests_return_401_without_bearer():
    client = TestClient(app)
    seen = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1/dental/"):
            continue
        if path in PUBLIC_PATHS or _is_patient_ai_route(path) or path in seen:
            continue
        seen.add(path)
        method = next(iter(getattr(route, "methods", {"GET"})))
        response = client.request(method, _sample_path(path))
        assert response.status_code == 401, (
            method,
            path,
            response.status_code,
            response.text,
        )


def test_demo_bearer_never_authenticates(monkeypatch):
    monkeypatch.delenv("DENTAL_DEMO_AUTH", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", ":memory:")
    from universal_intelligence_engine import dental_api

    dental_api._store = None
    response = TestClient(app).get(
        "/v1/dental/patients",
        headers={"Authorization": "Bearer demo"},
    )
    assert response.status_code == 401
