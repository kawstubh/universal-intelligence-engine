from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app

PUBLIC_OPERATIONS = {
    ("GET", "/v1/dental/health"),
    ("POST", "/v1/dental/auth/password/login"),
    ("POST", "/v1/dental/assistant/auth/login"),
    ("POST", "/v1/dental/public/appointments"),
    ("POST", "/v1/dental/public/demo-requests"),
    ("GET", "/v1/dental/billing/plans"),
    ("POST", "/v1/dental/billing/razorpay/webhook"),
}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}


def _is_public_operation(method: str, path: str) -> bool:
    return (method.upper(), path) in PUBLIC_OPERATIONS
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
    # The entire patient-account and linked-record surface is disabled.
    return (
        path.rstrip("/") in DISABLED_PATIENT_AI
        or path.startswith((PATIENT_PATH_PREFIX, PATIENT_DATA_PATH_PREFIX))
    )


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
        if not path.startswith("/v1/dental/") or _is_patient_ai_route(path):
            continue
        methods = {
            str(method).upper()
            for method in (getattr(route, "methods", None) or set())
            if str(method).upper() in HTTP_METHODS
        }
        public_methods = {method for method in methods if _is_public_operation(method, path)}
        protected_methods = methods - public_methods
        if not methods:
            continue

        dependency_calls = {
            getattr(dep.call, "__name__", "")
            for dep in getattr(route, "dependant", None).dependencies
        }
        auth_dependencies = {"require_doctor", "require_assistant", "require_patient"}
        if public_methods:
            assert not (dependency_calls & auth_dependencies), (
                "public operation must not inherit patient/doctor/assistant auth",
                sorted(public_methods),
                path,
                dependency_calls,
            )
        if not protected_methods:
            continue

        if path.startswith(ASSISTANT_PATH_PREFIX):
            expected_dependency = "require_assistant"
        elif path.startswith((PATIENT_PATH_PREFIX, PATIENT_DATA_PATH_PREFIX)):
            expected_dependency = "require_patient"
        else:
            expected_dependency = "require_doctor"
        for method in protected_methods:
            assert expected_dependency in dependency_calls, (
                "missing role-specific auth",
                method,
                path,
                expected_dependency,
            )


def test_protected_dental_requests_return_401_without_bearer():
    client = TestClient(app)
    seen = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1/dental/") or _is_patient_ai_route(path):
            continue
        methods = {
            str(method).upper()
            for method in (getattr(route, "methods", None) or set())
            if str(method).upper() in HTTP_METHODS
        }
        for method in sorted(methods):
            operation = (method, path)
            if _is_public_operation(method, path) or operation in seen:
                continue
            seen.add(operation)
            response = client.request(method, _sample_path(path))
            assert response.status_code == 401, (
                method,
                path,
                response.status_code,
                response.text,
            )



def test_patient_account_and_linked_record_routes_are_disabled_and_hidden_from_openapi():
    client = TestClient(app)
    disabled = [
        ("GET", "/v1/dental/patient/linked-records"),
        ("POST", "/v1/dental/patient/link"),
        ("GET", "/v1/dental/patient/appointments"),
        ("GET", "/v1/dental/ai/patient/profile"),
        ("POST", "/v1/dental/ai/patient/link"),
        ("POST", "/v1/dental/ai/patient/consent"),
    ]
    for method, path in disabled:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["detail"]["code"] == "PATIENT_ACCOUNT_FREE"

    schema = client.get("/openapi.json").json()
    assert not any(
        path.startswith(("/v1/dental/patient/", "/v1/dental/ai/patient/"))
        for path in schema.get("paths", {})
    )


def test_openapi_documents_bearer_security_for_protected_routes():
    schema = TestClient(app).get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }

    protected = {
        ("/v1/dental/assistant/appointments", "get"),
        ("/v1/dental/ai/doctor/patient-summary", "post"),
        ("/v1/dental/doctor/patients/{patient_id}/documents/upload-intent", "post"),
    }
    for path, method in protected:
        operation = schema["paths"][path][method]
        assert operation.get("security") == [{"BearerAuth": []}], (path, method, operation)
        assert not any(
            parameter.get("in") == "header"
            and str(parameter.get("name", "")).lower() == "authorization"
            for parameter in operation.get("parameters", [])
        )

    for path, method in (
        ("/v1/dental/public/appointments", "post"),
        ("/v1/dental/auth/password/login", "post"),
        ("/v1/dental/assistant/auth/login", "post"),
    ):
        operation = schema["paths"][path][method]
        assert "security" not in operation


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


def test_every_documented_protected_dental_operation_declares_bearer_auth():
    schema = TestClient(app).get("/openapi.json").json()
    paths = schema.get("paths", {})
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }

    http_methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    for public_method, public_path in PUBLIC_OPERATIONS:
        assert public_path in paths, f"public operation missing from OpenAPI: {public_path}"
        assert public_method.lower() in paths[public_path], (
            "public method missing from OpenAPI",
            public_method,
            public_path,
        )

    protected_count = 0
    for path, operations in paths.items():
        if not path.startswith("/v1/dental/"):
            continue
        assert not _is_patient_ai_route(path), f"disabled patient route leaked into OpenAPI: {path}"
        for method, operation in operations.items():
            normalized_method = method.lower()
            if normalized_method not in http_methods:
                continue
            if (normalized_method.upper(), path) in PUBLIC_OPERATIONS:
                assert operation.get("security") in (None, []), (
                    "public operation must not require BearerAuth",
                    method,
                    path,
                )
                continue
            protected_count += 1
            assert operation.get("security") == [{"BearerAuth": []}], (
                "every protected dental operation must explicitly require BearerAuth",
                method,
                path,
                operation.get("security"),
            )

    assert protected_count >= 1
