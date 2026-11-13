from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.api import app


@pytest.fixture(autouse=True)
def reset_public_intake_limits():
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()
    yield
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()


@pytest.fixture
def public_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "public-intake.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "dr-pranali")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dental_store._store = None
    with TestClient(app) as client:
        yield client
    dental_store._store = None


def _future_ist_time(days=7):
    # Use a published clinic slot so ordinary test requests pass validation.
    return (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=days)).replace(
        hour=10, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")


def _past_ist_time():
    return (datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=1)).replace(
        second=0, microsecond=0
    ).isoformat(timespec="seconds")


def _payload(*, key="a" * 32, name="Synthetic Patient", phone="+919999000001", starts_at=None, **extra):
    payload = {
        "idempotency_key": key,
        "name": name,
        "phone": phone,
        "starts_at": starts_at or _future_ist_time(),
        "treatment_type": "Dental check-up",
        "note": "",
        "intelligence_request": False,
    }
    payload.update(extra)
    return payload


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
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = public_client.post(
        "/v1/dental/public/appointments",
        headers={
            "CF-Connecting-IP": "203.0.113.44",
            "X-Forwarded-For": "198.51.100.99, 10.0.0.4",
        },
        json=_payload(key="b" * 32),
    )
    assert response.status_code == 200
    assert seen == ["203.0.113.44"]


def test_public_booking_rate_limit_uses_cloudflare_ipv6_client_ip(monkeypatch, public_client):
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = public_client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "2001:db8::42"},
        json=_payload(key="c" * 32, phone="+919999000003"),
    )
    assert response.status_code == 200
    assert seen == ["2001:db8::42"]


def test_public_booking_rate_limit_ignores_invalid_cloudflare_ip_header(monkeypatch, public_client):
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = public_client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "not-an-ip"},
        json=_payload(key="d" * 32, phone="+919999000002"),
    )
    assert response.status_code == 200
    assert seen == ["testclient"]


def test_public_booking_route_checks_client_rate_limit_before_persisting(monkeypatch, public_client):
    seen = []

    def reject(client_ip):
        seen.append(client_ip)
        raise HTTPException(
            status_code=429,
            detail={"code": "PUBLIC_APPOINTMENT_RATE_LIMITED"},
            headers={"Retry-After": "300"},
        )

    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", reject)
    response = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="e" * 32, phone="+919999000004"),
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert response.headers["retry-after"] == "300"
    assert seen == ["testclient"]
    assert dental_store.get_dental_store().patients("dr-pranali") == []


def test_public_booking_is_idempotent_and_never_exposes_internal_ids(public_client):
    store = dental_store.get_dental_store()
    before_patients = len(store.patients("dr-pranali"))
    before_appointments = len(store.appointments("dr-pranali"))
    payload = _payload(key="f" * 32)

    first = public_client.post("/v1/dental/public/appointments", json=payload)
    retry = public_client.post("/v1/dental/public/appointments", json=payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    first_body = first.json()
    retry_body = retry.json()
    assert first_body["status"] == "requested"
    assert first_body["reference"].startswith("ORL-")
    assert first_body["reference"] == retry_body["reference"]
    assert "patient_id" not in first_body and "appointment_id" not in first_body
    assert "appointment" not in first_body and "patient" not in first_body
    assert len(store.patients("dr-pranali")) == before_patients + 1
    assert len(store.appointments("dr-pranali")) == before_appointments + 1


def test_public_booking_duplicate_fingerprint_prevents_second_pending_request(public_client):
    store = dental_store.get_dental_store()
    payload = _payload(key="g" * 32, note="Tooth pain")

    first = public_client.post("/v1/dental/public/appointments", json=payload)
    duplicate = public_client.post(
        "/v1/dental/public/appointments",
        json={**payload, "idempotency_key": "h" * 32, "note": "Pain when chewing"},
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_received"
    assert "reference" not in duplicate.json()
    assert len(store.patients("dr-pranali")) == 1
    assert len(store.appointments("dr-pranali")) == 1


def test_public_booking_rejects_idempotency_key_reuse_for_changed_payload(public_client):
    payload = _payload(key="i" * 32)
    first = public_client.post("/v1/dental/public/appointments", json=payload)
    changed = public_client.post(
        "/v1/dental/public/appointments",
        json={**payload, "note": "A different request"},
    )

    assert first.status_code == 200
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(dental_store.get_dental_store().patients("dr-pranali")) == 1


def test_public_booking_requires_future_ist_datetime_and_approved_service(public_client):
    missing_timezone = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="j" * 32, starts_at=_future_ist_time().replace("+05:30", "")),
    )
    utc_time = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="k" * 32, starts_at="2026-12-01T10:00:00Z"),
    )
    past_time = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="l" * 32, starts_at=_past_ist_time()),
    )
    unknown_service = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="m" * 32, treatment_type="Dental consultation"),
    )
    invalid_time_slot = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="p" * 32, starts_at=_future_ist_time().split("T")[0] + "T16:00:00+05:30"),
    )

    assert missing_timezone.status_code == 422
    assert utc_time.status_code == 422
    assert past_time.status_code == 422
    assert unknown_service.status_code == 422
    assert invalid_time_slot.status_code == 422


def test_public_booking_rejects_patient_ai_requests(public_client):
    response = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="n" * 32, intelligence_request=True),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PUBLIC_AI_DISABLED"
    assert dental_store.get_dental_store().patients("dr-pranali") == []


def test_public_booking_honeypot_does_not_create_records(public_client):
    response = public_client.post(
        "/v1/dental/public/appointments",
        json=_payload(key="o" * 32, website="https://spam.example"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert "reference" not in response.json()
    assert dental_store.get_dental_store().patients("dr-pranali") == []
    assert dental_store.get_dental_store().appointments("dr-pranali") == []


def test_public_booking_requires_idempotency_key(public_client):
    payload = _payload()
    payload.pop("idempotency_key")
    response = public_client.post("/v1/dental/public/appointments", json=payload)
    assert response.status_code == 422
    assert dental_store.get_dental_store().patients("dr-pranali") == []
