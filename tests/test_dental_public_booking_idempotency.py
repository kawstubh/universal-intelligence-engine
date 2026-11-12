from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.api import app
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def guest_booking_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "guest-booking.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-public")
    store = DentalStore()
    store.create_organisation(
        {"id": "org-public", "name": "Guest Booking Test", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-public",
            "organisation_id": "org-public",
            "name": "Guest Booking Test Clinic",
            "clinic_type": "clinic",
        }
    )
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    dental_api._PUBLIC_APPOINTMENT_ATTEMPTS.clear()
    return TestClient(app), store


def _payload(**overrides):
    value = {
        "idempotency_key": uuid4().hex,
        "name": "Synthetic Guest",
        "phone": "+919999000001",
        "starts_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        "treatment_type": "Dental check-up",
        "note": "Synthetic test note",
        "intelligence_request": False,
    }
    value.update(overrides)
    return value


def test_guest_booking_requires_a_timezone_aware_future_start(guest_booking_api):
    client, store = guest_booking_api
    naive = client.post(
        "/v1/dental/public/appointments",
        json=_payload(starts_at=(datetime.now(UTC) + timedelta(days=2)).replace(tzinfo=None).isoformat()),
    )
    past = client.post(
        "/v1/dental/public/appointments",
        json=_payload(starts_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat()),
    )

    assert naive.status_code == 422
    assert past.status_code == 422
    assert store._execute("SELECT id FROM dental_patients") == []
    assert store._execute("SELECT id FROM dental_appointments") == []


def test_guest_booking_requires_idempotency_and_rejects_patient_ai(guest_booking_api):
    client, store = guest_booking_api
    missing_key = _payload()
    missing_key.pop("idempotency_key")
    no_key = client.post("/v1/dental/public/appointments", json=missing_key)
    ai_request = client.post(
        "/v1/dental/public/appointments",
        json=_payload(idempotency_key=uuid4().hex, intelligence_request=True),
    )

    assert no_key.status_code == 422
    assert ai_request.status_code == 422
    assert store._execute("SELECT id FROM dental_patients") == []
    assert store._execute("SELECT id FROM dental_appointments") == []


def test_guest_booking_retry_is_idempotent_and_never_returns_patient_id(guest_booking_api):
    client, store = guest_booking_api
    payload = _payload()

    first = client.post("/v1/dental/public/appointments", json=payload)
    second = client.post("/v1/dental/public/appointments", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["duplicate"] is False
    assert second_body["duplicate"] is True
    assert first_body["appointment"]["id"] == second_body["appointment"]["id"]
    assert "patient_id" not in first_body["appointment"]
    assert "patient_id" not in second_body["appointment"]
    assert len(store._execute("SELECT id FROM dental_patients")) == 1
    assert len(store._execute("SELECT id FROM dental_appointments")) == 1

    audit_rows = store._execute(
        "SELECT action,metadata_json FROM dental_audit_events ORDER BY created_at"
    )
    assert [row["action"] for row in audit_rows] == ["request", "request.duplicate"]
    assert all(row["metadata_json"] == "{}" for row in audit_rows)


def test_reusing_guest_idempotency_key_for_different_payload_returns_conflict(guest_booking_api):
    client, store = guest_booking_api
    payload = _payload()
    first = client.post("/v1/dental/public/appointments", json=payload)
    changed = dict(payload, phone="+919999000002")
    second = client.post("/v1/dental/public/appointments", json=changed)

    assert first.status_code == 200, first.text
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(store._execute("SELECT id FROM dental_patients")) == 1
    assert len(store._execute("SELECT id FROM dental_appointments")) == 1


def test_idempotent_retry_returns_existing_appointment_after_clinic_status_change(guest_booking_api):
    client, store = guest_booking_api
    payload = _payload()
    first = client.post("/v1/dental/public/appointments", json=payload)
    appointment_id = first.json()["appointment"]["id"]
    updated = store.update_appointment(
        appointment_id, {"status": "confirmed"}, "clinic-public"
    )
    retry = client.post("/v1/dental/public/appointments", json=payload)

    assert updated["status"] == "confirmed"
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["appointment"]["id"] == appointment_id
    assert retry.json()["appointment"]["status"] == "confirmed"
    assert len(store._execute("SELECT id FROM dental_patients")) == 1
    assert len(store._execute("SELECT id FROM dental_appointments")) == 1
