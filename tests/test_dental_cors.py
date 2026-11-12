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
