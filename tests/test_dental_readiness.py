from fastapi.testclient import TestClient

from universal_intelligence_engine import api


class HealthyStore:
    backend = "postgres"

    def healthcheck(self):
        return True


class UnhealthyStore:
    backend = "postgres"

    def healthcheck(self):
        raise RuntimeError("database unavailable")


def test_readiness_returns_ready_when_database_is_reachable(monkeypatch):
    monkeypatch.setattr(api, "get_dental_store", lambda: HealthyStore())

    response = TestClient(api.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "component": "dental_api",
        "database": "postgres",
    }


def test_readiness_fails_closed_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(api, "get_dental_store", lambda: UnhealthyStore())

    response = TestClient(api.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["component"] == "dental_database"
