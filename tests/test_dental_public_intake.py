import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.api import app


@pytest.fixture(autouse=True)
def reset_public_intake_limits():
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()
    yield
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()


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


def test_public_booking_route_checks_client_rate_limit_before_persisting(monkeypatch):
    seen = []

    def reject(client_ip):
        seen.append(client_ip)
        raise HTTPException(
            status_code=429,
            detail={"code": "PUBLIC_APPOINTMENT_RATE_LIMITED"},
            headers={"Retry-After": "300"},
        )

    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", reject)
    response = TestClient(app).post(
        "/v1/dental/public/appointments",
        json={
            "name": "Synthetic Patient",
            "phone": "+919999000001",
            "starts_at": "2026-11-20T10:00:00",
            "treatment_type": "Dental check-up",
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert response.headers["retry-after"] == "300"
    assert seen == ["testclient"]
