from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.api import app
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def public_booking_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "public-booking.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "dr-pranali")
    monkeypatch.setenv("DENTAL_PUBLIC_BOOKING_HASH_KEY", "synthetic-public-booking-hash-key-32-bytes-minimum")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    monkeypatch.setattr(dental_store, "_store", None)
    store = DentalStore()
    store.create_organisation(
        {"id": "org-public", "name": "Synthetic Public Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "dr-pranali",
            "organisation_id": "org-public",
            "name": "Synthetic Public Clinic",
            "clinic_type": "clinic",
        }
    )
    client = TestClient(app)
    yield client, store
    monkeypatch.setattr(dental_store, "_store", None)


def _payload(
    key: str = "public-booking-key-0001",
    phone: str = "+919999000001",
    starts_at: str | None = None,
) -> dict:
    future = datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=7)
    default_slot = future.replace(hour=10, minute=0, second=0, microsecond=0)
    preferred = starts_at or default_slot.isoformat()
    return {
        "idempotency_key": key,
        "name": "Synthetic Patient",
        "phone": phone,
        "starts_at": preferred,
        "treatment_type": "Dental Check-up & Consultation",
        "intelligence_request": False,
    }


def _count(store: DentalStore, table: str) -> int:
    return int(store._execute(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])


def test_public_appointment_rate_limit_blocks_bursts_and_returns_retry_after(
    monkeypatch, public_booking_api
):
    from universal_intelligence_engine import dental_rate_limit

    _client, _store = public_booking_api
    monkeypatch.setattr(dental_rate_limit.time, "time", lambda: 1000)
    for _ in range(8):
        dental_api._check_public_appointment_rate_limit("203.0.113.10")
    with pytest.raises(HTTPException) as limited:
        dental_api._check_public_appointment_rate_limit("203.0.113.10")

    assert limited.value.status_code == 429
    assert limited.value.detail["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert limited.value.headers["Retry-After"] == "200"

    monkeypatch.setattr(dental_rate_limit.time, "time", lambda: 1200)
    dental_api._check_public_appointment_rate_limit("203.0.113.10")


def test_public_appointment_rate_limit_isolated_per_client_ip(public_booking_api):
    _client, _store = public_booking_api
    for _ in range(8):
        dental_api._check_public_appointment_rate_limit("203.0.113.11")
    dental_api._check_public_appointment_rate_limit("203.0.113.12")
    with pytest.raises(HTTPException) as limited:
        dental_api._check_public_appointment_rate_limit("203.0.113.11")
    assert limited.value.status_code == 429


def test_public_booking_rate_limit_uses_cloudflare_client_ip(monkeypatch, public_booking_api):
    client, _store = public_booking_api
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={
            "CF-Connecting-IP": "203.0.113.44",
            "X-Forwarded-For": "198.51.100.99, 10.0.0.4",
        },
        json=_payload(key="public-booking-ipv4-0001"),
    )
    assert response.status_code == 200, response.text
    assert seen == ["203.0.113.44"]


def test_public_booking_rate_limit_uses_cloudflare_ipv6_client_ip(monkeypatch, public_booking_api):
    client, _store = public_booking_api
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "2001:db8::42"},
        json=_payload(key="public-booking-ipv6-0001", phone="+919999000003"),
    )
    assert response.status_code == 200, response.text
    assert seen == ["2001:db8::42"]


def test_public_booking_rate_limit_ignores_invalid_cloudflare_ip_header(monkeypatch, public_booking_api):
    client, _store = public_booking_api
    seen = []
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", seen.append)
    response = client.post(
        "/v1/dental/public/appointments",
        headers={"CF-Connecting-IP": "not-an-ip"},
        json=_payload(key="public-booking-invalid-ip-001", phone="+919999000002"),
    )
    assert response.status_code == 200, response.text
    assert seen == ["testclient"]


def test_public_booking_route_checks_client_rate_limit_before_persisting(monkeypatch, public_booking_api):
    client, store = public_booking_api
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
        json=_payload(key="public-booking-rate-reject-001"),
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "PUBLIC_APPOINTMENT_RATE_LIMITED"
    assert response.headers["retry-after"] == "300"
    assert seen == ["testclient"]
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0


def test_public_booking_is_idempotent_and_does_not_expose_patient_or_appointment_ids(public_booking_api):
    client, store = public_booking_api
    payload = _payload(key="public-booking-idempotent-001")
    first = client.post("/v1/dental/public/appointments", json=payload)
    replay = client.post("/v1/dental/public/appointments", json=payload)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["status"] == "requested"
    assert replay.json()["status"] == "already_received"
    assert first.json()["reference"] == replay.json()["reference"]
    assert first.json()["reference"].startswith("ORL-")
    for key in ("patient_id", "appointment_id", "patient", "appointment", "id"):
        assert key not in first.json()
    assert _count(store, "dental_patients") == 1
    assert _count(store, "dental_appointments") == 1
    assert _count(store, "dental_public_booking_requests") == 1
    patient = store._execute("SELECT age FROM dental_patients")[0]
    appointment = store._execute("SELECT note,status FROM dental_appointments")[0]
    assert patient["age"] == ""
    assert appointment["note"] == ""
    assert appointment["status"] == "requested"


def test_public_booking_rejects_changed_key_payload_and_same_phone_time_duplicates(public_booking_api):
    client, store = public_booking_api
    payload = _payload(key="public-booking-conflict-001")
    first = client.post("/v1/dental/public/appointments", json=payload)
    assert first.status_code == 200

    changed_payload = {**payload, "name": "Different Synthetic Person"}
    changed = client.post("/v1/dental/public/appointments", json=changed_payload)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    duplicate = client.post(
        "/v1/dental/public/appointments",
        json={**payload, "idempotency_key": "public-booking-conflict-002"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "DUPLICATE_APPOINTMENT_REQUEST"
    assert _count(store, "dental_patients") == 1
    assert _count(store, "dental_appointments") == 1
    assert _count(store, "dental_public_booking_requests") == 1


def test_public_booking_requires_timezone_aware_future_time_and_fixed_service_catalog(public_booking_api):
    client, store = public_booking_api
    base = _payload(key="public-booking-validation-001")

    naive = client.post(
        "/v1/dental/public/appointments",
        json={**base, "starts_at": "2026-11-20T10:00:00"},
    )
    assert naive.status_code == 422

    past = (
        datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=1)
    ).replace(second=0, microsecond=0).isoformat()
    expired = client.post(
        "/v1/dental/public/appointments",
        json={**base, "idempotency_key": "public-booking-validation-002", "starts_at": past},
    )
    assert expired.status_code == 422

    invalid_phone = client.post(
        "/v1/dental/public/appointments",
        json={**base, "idempotency_key": "public-booking-validation-003", "phone": "1234567890"},
    )
    assert invalid_phone.status_code == 422

    unapproved_service = client.post(
        "/v1/dental/public/appointments",
        json={**base, "idempotency_key": "public-booking-validation-005", "treatment_type": "Invented service"},
    )
    assert unapproved_service.status_code == 422

    patient_ai = client.post(
        "/v1/dental/public/appointments",
        json={**base, "idempotency_key": "public-booking-validation-006", "intelligence_request": True},
    )
    assert patient_ai.status_code == 422
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0


def test_public_booking_honeypot_is_accepted_without_persisting(public_booking_api):
    client, store = public_booking_api
    response = client.post(
        "/v1/dental/public/appointments",
        json={**_payload(key="public-booking-honeypot-001"), "website": "https://spam.example"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0
    assert _count(store, "dental_public_booking_requests") == 0

def test_public_booking_accepts_all_twenty_clinic_approved_services(monkeypatch, public_booking_api):
    client, store = public_booking_api
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", lambda _client_ip: None)
    services = [
        "Dental Check-up & Consultation",
        "Scaling & Cleaning",
        "Teeth Whitening",
        "Tooth Filling",
        "Root Canal Treatment",
        "Dental Crowns & Caps",
        "Dental Implants",
        "Tooth Extraction",
        "Braces Treatment",
        "Clear Aligners",
        "Dentures",
        "Veneers",
        "Gum Treatment (Periodontal Care)",
        "Kids Dentistry",
        "Emergency Dental Care",
        "Tooth Sensitivity Treatment",
        "Dental Sealants",
        "Oral Hygiene Education",
        "Smile Makeover",
        "TMJ & Jaw Pain Treatment",
    ]
    for index, service in enumerate(services):
        response = client.post(
            "/v1/dental/public/appointments",
            json={
                **_payload(
                    key=f"public-booking-catalog-{index:02d}",
                    phone=f"+9199990000{index:02d}",
                ),
                "treatment_type": service,
            },
        )
        assert response.status_code == 200, (service, response.text)
        assert response.json()["status"] == "requested"
    assert _count(store, "dental_patients") == 20
    assert _count(store, "dental_appointments") == 20
    assert _count(store, "dental_public_booking_requests") == 20


def test_public_booking_keeps_other_concern_as_a_safe_fallback(public_booking_api):
    client, store = public_booking_api
    response = client.post(
        "/v1/dental/public/appointments",
        json={
            **_payload(key="public-booking-other-concern-001"),
            "treatment_type": "Other dental concern",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "requested"
    assert _count(store, "dental_patients") == 1
    assert _count(store, "dental_appointments") == 1


def test_public_booking_fails_closed_without_hash_key(public_booking_api, monkeypatch):
    client, store = public_booking_api
    monkeypatch.delenv("DENTAL_PUBLIC_BOOKING_HASH_KEY", raising=False)
    response = client.post("/v1/dental/public/appointments", json=_payload(key="public-booking-no-hmac-key-001"))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PUBLIC_BOOKING_HASH_NOT_CONFIGURED"
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0
    assert _count(store, "dental_public_booking_requests") == 0


def test_public_booking_phone_fingerprint_is_keyed(public_booking_api):
    client, store = public_booking_api
    payload = _payload(key="public-booking-hmac-check-001")
    response = client.post("/v1/dental/public/appointments", json=payload)
    assert response.status_code == 200, response.text
    row = store._execute(
        "SELECT phone_hash FROM dental_public_booking_requests",
    )[0]
    phone_digits = "".join(character for character in payload["phone"] if character.isdigit())
    import hashlib
    assert row["phone_hash"] != hashlib.sha256(phone_digits.encode("utf-8")).hexdigest()
    assert len(row["phone_hash"]) == 64

def test_public_booking_rejects_legacy_expanded_service_aliases(public_booking_api):
    client, store = public_booking_api
    legacy_aliases = [
        "Root Canal Treatment (RCT)",
        "Clear Aligners (Invisible Braces)",
        "Dentures (Full / Partial)",
        "Veneers (Smile Makeover)",
        "Kids Dentistry (Child Care)",
        "Dental Sealants (Cavity Prevention)",
        "Smile Makeover (Cosmetic Dentistry)",
    ]
    for index, service in enumerate(legacy_aliases):
        response = client.post(
            "/v1/dental/public/appointments",
            json={
                **_payload(
                    key=f"public-booking-legacy-alias-{index:02d}",
                    phone=f"+9199990010{index:02d}",
                ),
                "treatment_type": service,
            },
        )
        assert response.status_code == 422, (service, response.text)
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0
    assert _count(store, "dental_public_booking_requests") == 0



def test_public_booking_accepts_only_eight_exact_ist_clinic_slots(monkeypatch, public_booking_api):
    client, store = public_booking_api
    monkeypatch.setattr(dental_api, "_check_public_appointment_rate_limit", lambda _ip: None)
    clinic_date = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=7)).date()
    slots = [10, 11, 12, 13, 17, 18, 19, 20]

    for index, hour in enumerate(slots):
        local_slot = datetime(
            clinic_date.year, clinic_date.month, clinic_date.day,
            hour, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        # Exercise offset conversion as well as the normal +05:30 form.
        starts_at = (
            local_slot.astimezone(ZoneInfo("UTC")).isoformat()
            if hour == 10
            else local_slot.isoformat()
        )
        response = client.post(
            "/v1/dental/public/appointments",
            json=_payload(
                key=f"public-booking-allowed-slot-{index:02d}",
                phone=f"+9199980000{index:02d}",
                starts_at=starts_at,
            ),
        )
        assert response.status_code == 200, (hour, response.text)
        assert response.json()["status"] == "requested"

    assert _count(store, "dental_appointments") == len(slots)


@pytest.mark.parametrize(
    "local_time",
    [
        "09:00:00",
        "10:30:00",
        "13:30:00",
        "14:00:00",
        "15:00:00",
        "16:00:00",
        "16:30:00",
        "20:30:00",
        "21:00:00",
        "22:00:00",
    ],
)
def test_public_booking_rejects_times_outside_exact_ist_slots(public_booking_api, local_time):
    client, store = public_booking_api
    clinic_date = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=7)).date()
    starts_at = f"{clinic_date.isoformat()}T{local_time}+05:30"
    response = client.post(
        "/v1/dental/public/appointments",
        json=_payload(
            key=f"public-booking-rejected-{local_time.replace(':', '')}",
            phone="+919997000001",
            starts_at=starts_at,
        ),
    )
    assert response.status_code == 422, response.text
    assert _count(store, "dental_patients") == 0
    assert _count(store, "dental_appointments") == 0
