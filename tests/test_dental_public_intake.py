from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.api import app
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture(autouse=True)
def reset_public_intake_limits():
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()
    yield
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()


@pytest.fixture
def public_client(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "public-intake.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-public")
    store = DentalStore()
    store.create_organisation(
        {"id": "org-public", "name": "Public Intake Test", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-public",
            "organisation_id": "org-public",
            "name": "Public Intake Test Clinic",
            "clinic_type": "clinic",
        }
    )
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    return TestClient(app), store


def _booking_body(phone: str = "+919999000001", **overrides):
    body = {
        "idempotency_key": uuid4().hex,
        "name": "Synthetic Patient",
        "phone": phone,
        "starts_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "treatment_type": "Dental check-up",
        "intelligence_request": False,
    }
    body.update(overrides)
    return body


def test_public_appointment_rate_limit_blocks_bursts_and_returns_retry_after(monkeypatch):
    monkeypatch.setattr(dental_api, "_PUBLIC_APPOINTMENT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(dental_api, "_PUBLIC_APPOINTMENT_WINDOW_SECONDS", 300)
    clock = {"now": 1000.0}
    monkeypatch.setattr(dental_api.time, "monotonic", lambda: clock["now"])

    dental_api._check_public_appointment_rate_limit("203.0.113.10")
    dental_api._check_public_appointment_rate_limit("203.0.113.10")
    with pytest.raises(HTTPException) as limited:
        dental_api._check_public_appointment_rate_limit("203.0.113.10")

    assert limited.value.status_code == 429
    assert limited.value.detail["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert limited.value.headers["Retry-After"] == "300"

    clock["now"] += 301
    dental_api._check_public_appointment_rate_limit("203.0.113.10")


def test_public_appointment_rate_limit_isolated_per_client_ip(monkeypatch):
    monkeypatch.setattr(dental_api, "_PUBLIC_APPOINTMENT_MAX_ATTEMPTS", 1)
    dental_api._check_public_appointment_rate_limit("203.0.113.11")
    dental_api._check_public_appointment_rate_limit("203.0.113.12")
    with pytest.raises(HTTPException) as limited:
        dental_api._check_public_appointment_rate_limit("203.0.113.11")
    assert limited.value.status_code == 429


def test_public_booking_rate_limit_uses_cloudflare_client_ip(monkeypatch, public_client):
    client, _ = public_client
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={
            "CF-Connecting-IP": "203.0.113.44",
            "X-Forwarded-For": "198.51.100.99, 10.0.0.4",
        },
        json=_booking_body(),
    )
    assert response.status_code == 200, response.text
    assert seen == ["203.0.113.44"]


def test_public_booking_rate_limit_uses_cloudflare_ipv6_client_ip(monkeypatch, public_client):
    client, _ = public_client
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "2001:db8::42"},
        json=_booking_body(phone="+919999000003"),
    )
    assert response.status_code == 200, response.text
    assert seen == ["2001:db8::42"]


def test_public_booking_rate_limit_ignores_invalid_cloudflare_ip_header(monkeypatch, public_client):
    client, _ = public_client
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "not-an-ip"},
        json=_booking_body(phone="+919999000002"),
    )
    assert response.status_code == 200, response.text
    assert seen == ["testclient"]


def test_public_booking_route_checks_client_rate_limit_before_persisting(monkeypatch, public_client):
    client, store = public_client
    seen = []

    def reject(client_ip):
        seen.append(client_ip)
        raise HTTPException(
            status_code=429,
            detail={"code": "PUBLIC_APPOINTMENT_RATE_LIMITED"},
            headers={"Retry-After": "300"},
        )

    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", reject)
    response = client.post(
        "/v1/dental/public/appointments",
        json=_booking_body(),
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert response.headers["retry-after"] == "300"
    assert seen == ["testclient"]
    assert store._execute("SELECT id FROM dental_patients") == []
    assert store._execute("SELECT id FROM dental_appointments") == []
