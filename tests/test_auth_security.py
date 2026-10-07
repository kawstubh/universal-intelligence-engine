from fastapi.testclient import TestClient
from universal_intelligence_engine.api import app


PUBLIC_PATHS = {
    "/v1/dental/health",
    "/v1/dental/auth/google",
    "/v1/dental/public/appointments",
    "/v1/dental/billing/plans",
    "/v1/dental/billing/razorpay/webhook",
}


def _sample_path(path: str) -> str:
    return path.replace("{appointment_id}", "missing-appointment").replace("{patient_id}", "missing-patient")


def test_no_demo_or_otp_release_auth_symbols():
    import universal_intelligence_engine.dental_auth as auth
    import universal_intelligence_engine.dental_api as api

    assert not hasattr(auth, "DEMO_AUTH")
    assert not hasattr(auth, "DEMO_OTP")
    assert not hasattr(auth, "request_otp")
    assert not hasattr(auth, "verify_otp")
    assert "otp/request" not in api.__dict__.get("router").routes.__repr__().lower()


def test_protected_dental_routes_require_auth_dependency():
    from universal_intelligence_engine.dental_auth import require_doctor

    for route in app.routes:
        if not getattr(route, "path", "").startswith("/v1/dental/"):
            continue
        if route.path in PUBLIC_PATHS:
            continue
        dependency_calls = {
            getattr(dep.call, "__name__", "")
            for dep in getattr(route, "dependant", None).dependencies
        }
        assert "require_doctor" in dependency_calls, f"missing auth on {route.path}"


def test_protected_dental_requests_return_401_without_bearer():
    client = TestClient(app)
    seen = set()
    for route in app.routes:
        if not getattr(route, "path", "").startswith("/v1/dental/"):
            continue
        if route.path in PUBLIC_PATHS or route.path in seen:
            continue
        seen.add(route.path)
        method = next(iter(getattr(route, "methods", {"GET"})))
        response = client.request(method, _sample_path(route.path))
        assert response.status_code == 401, (method, route.path, response.status_code, response.text)


def test_demo_bearer_never_authenticates(monkeypatch):
    monkeypatch.delenv("DENTAL_DEMO_AUTH", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", ":memory:")
    import universal_intelligence_engine.dental_api as dental_api
    dental_api._store = None
    response = TestClient(app).get(
        "/v1/dental/patients",
        headers={"Authorization": "Bearer demo"},
    )
    assert response.status_code == 401
