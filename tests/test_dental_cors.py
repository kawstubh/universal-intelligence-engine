from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app


def test_local_oralyn_preflight_allows_authorized_browser_requests():
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
