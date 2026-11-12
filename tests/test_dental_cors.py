import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine.api import _parse_cors_origins, app


def test_cors_configuration_fails_closed_when_unset(monkeypatch):
    monkeypatch.delenv("DENTAL_CORS_ORIGINS", raising=False)
    assert _parse_cors_origins(None) == []
    assert _parse_cors_origins("") == []


def test_cors_configuration_accepts_only_exact_canonical_origins():
    assert _parse_cors_origins(
        "https://portal.example,https://portal.example,http://localhost:5173"
    ) == ["https://portal.example", "http://localhost:5173"]


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*.example",
        "https://portal.example/",
        "https://portal.example/path",
        "https://user:password@portal.example",
        "ftp://portal.example",
        "https://portal.example?next=/",
        "https://portal.example:invalid",
    ],
)
def test_cors_configuration_rejects_non_origin_entries(value):
    with pytest.raises(ValueError):
        _parse_cors_origins(value)


def test_local_oralyn_preflight_allows_explicit_test_origin():
    client = TestClient(app)
    response = client.options(
        "/v1/dental/auth/password/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-credentials"] == "true"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "strict-transport-security" in exposed
    assert "x-content-type-options" in exposed


def test_unlisted_browser_origin_is_not_reflected():
    client = TestClient(app)
    response = client.options(
        "/v1/dental/auth/password/login",
        headers={
            "Origin": "https://unlisted.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers

def test_dental_api_responses_are_not_cached_and_use_nosniff():
    client = TestClient(app)
    response = client.get("/v1/dental/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_disabled_patient_ai_response_is_also_not_cacheable():
    client = TestClient(app)
    response = client.post("/v1/dental/ai/patient/chat", json={})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PATIENT_AI_DISABLED"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
