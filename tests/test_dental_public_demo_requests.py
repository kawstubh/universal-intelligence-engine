from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.dental_store import DentalStore


def _client(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "demo-requests.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("ALEMBIC_CONFIG", str(root / "alembic.ini"))
    store = DentalStore()
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "enforce_shared_rate_limit", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(dental_api.router)
    return TestClient(app), store


def test_public_demo_request_is_pending_and_idempotent(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    payload = {
        "idempotency_key": "demo-request-key-000001",
        "clinic_name": "Sample Dental Clinic",
        "owner_name": "Clinic Owner",
        "email": "owner@example.test",
        "phone": "9876543210",
    }

    first = client.post("/v1/dental/public/demo-requests", json=payload)
    second = client.post("/v1/dental/public/demo-requests", json=payload)
    rows = store._execute("SELECT id,status FROM dental_onboarding_requests")

    assert first.status_code == 202
    assert first.json()["status"] == "received"
    assert second.status_code == 202
    assert second.json()["status"] == "already_received"
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_demo_request_honeypot_is_not_persisted(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/dental/public/demo-requests",
        json={
            "idempotency_key": "demo-request-key-000002",
            "clinic_name": "Spam Dental",
            "owner_name": "Bot",
            "email": "bot@example.test",
            "phone": "9876543210",
            "website": "https://spam.example",
        },
    )
    rows = store._execute("SELECT id FROM dental_onboarding_requests")

    assert response.status_code == 202
    assert response.json()["status"] == "received"
    assert rows == []
