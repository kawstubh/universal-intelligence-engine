from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import (
    dental_ai,
    dental_api,
    dental_assistant,
    dental_auth,
    dental_documents,
    dental_patient_care,
    dental_patient_comms,
)
from universal_intelligence_engine.dental_store import DentalStore


OWNER_EMAIL = "owner@example.test"
OWNER_PASSWORD = "owner-password-strong"
ASSISTANT_EMAIL = "reception@example.test"
ASSISTANT_PASSWORD = "assistant-password-strong"


@pytest.fixture
def assistant_api(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "clinic-assistant.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )

    store = DentalStore()
    store.create_organisation(
        {"id": "org-a", "name": "Clinic Group", "organisation_type": "group"}
    )
    store.create_organisation(
        {"id": "org-b", "name": "Other Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Main Clinic",
            "clinic_type": "clinic",
        }
    )
    store.create_clinic(
        {
            "id": "clinic-b",
            "organisation_id": "org-b",
            "name": "Other Clinic",
            "clinic_type": "clinic",
        }
    )
    store.create_doctor(
        {
            "id": "doctor-owner",
            "email": OWNER_EMAIL,
            "password_hash": dental_auth.hash_password(OWNER_PASSWORD),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-owner", "clinic-a", "owner")

    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_assistant, "get_dental_store", lambda: store)
    dental_auth._LOGIN_ATTEMPTS.clear()
    dental_assistant._LOGIN_ATTEMPTS.clear()

    app = FastAPI()
    app.include_router(dental_api.router)
    app.include_router(dental_assistant.router)
    app.include_router(dental_ai.router)
    app.include_router(dental_patient_care.router)
    app.include_router(dental_documents.router)
    app.include_router(dental_patient_comms.router)
    client = TestClient(app)
    return client, store


def _owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/dental/auth/password/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_assistant(client: TestClient) -> dict:
    created = client.post(
        "/v1/dental/auth/assistants",
        headers=_owner_headers(client),
        json={"email": ASSISTANT_EMAIL, "password": ASSISTANT_PASSWORD},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _assistant_headers(client: TestClient) -> tuple[dict[str, str], dict]:
    login = client.post(
        "/v1/dental/auth/assistant/login",
        json={"email": ASSISTANT_EMAIL, "password": ASSISTANT_PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "assistant"
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, login.json()


def _patient(store: DentalStore, patient_id: str, clinic_id: str) -> dict:
    return store.create_patient(
        {"name": f"Synthetic {patient_id}", "phone": "9000000000", "age": ""},
        clinic_id,
    )


def test_assistant_is_a_separate_principal_and_has_its_own_login(assistant_api):
    client, store = assistant_api
    created = _create_assistant(client)

    listed = client.get(
        "/v1/dental/auth/assistants", headers=_owner_headers(client)
    )
    assert listed.status_code == 200
    assert len(listed.json()["assistants"]) == 1
    assert listed.json()["assistants"][0]["role"] == "assistant"
    assert "password_hash" not in listed.json()["assistants"][0]

    assert created["role"] == "assistant"
    assert store.doctor_by_email(ASSISTANT_EMAIL) is None
    assistant_rows = store._execute(
        "SELECT id,email,home_clinic_id FROM dental_clinic_assistants WHERE email=?",
        (ASSISTANT_EMAIL,),
    )
    membership_rows = store._execute(
        "SELECT role,clinic_id FROM dental_clinic_assistant_memberships WHERE assistant_id=?",
        (created["id"],),
    )
    assert len(assistant_rows) == 1
    assert membership_rows == [{"role": "assistant", "clinic_id": "clinic-a"}]

    doctor_login = client.post(
        "/v1/dental/auth/password/login",
        json={"email": ASSISTANT_EMAIL, "password": ASSISTANT_PASSWORD},
    )
    assert doctor_login.status_code == 401

    headers, login = _assistant_headers(client)
    assert login["user"] == {
        "id": created["id"],
        "email": ASSISTANT_EMAIL,
        "role": "assistant",
        "clinic_id": "clinic-a",
    }
    me = client.get("/v1/dental/auth/assistant/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "assistant"


def test_assistant_can_manage_contact_intake_and_reception_queue(assistant_api):
    client, store = assistant_api
    assistant = _create_assistant(client)
    headers, _login = _assistant_headers(client)
    existing = _patient(store, "patient-a", "clinic-a")

    directory = client.get("/v1/dental/assistant/patients", headers=headers)
    assert directory.status_code == 200
    assert directory.json()["patients"] == [
        {"id": existing["id"], "name": existing["name"], "phone": existing["phone"]}
    ]
    assert "age" not in directory.json()["patients"][0]
    assert "created_at" not in directory.json()["patients"][0]

    profile = client.get(
        f"/v1/dental/assistant/patients/{existing['id']}", headers=headers
    )
    assert profile.status_code == 200
    assert set(profile.json()) == {"id", "name", "phone"}

    intake = client.post(
        "/v1/dental/assistant/patients",
        headers=headers,
        json={"name": "Synthetic Reception Intake", "phone": "9111111111"},
    )
    assert intake.status_code == 201, intake.text
    assert set(intake.json()) == {"id", "name", "phone"}

    starts_at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    booking = client.post(
        "/v1/dental/assistant/appointments",
        headers=headers,
        json={
            "patient_id": existing["id"],
            "starts_at": starts_at,
            "treatment_type": "Dental check-up",
        },
    )
    assert booking.status_code == 201, booking.text
    appointment = booking.json()
    assert appointment["status"] == "requested"
    assert "note" not in appointment

    queue = client.get("/v1/dental/assistant/appointments", headers=headers)
    assert queue.status_code == 200
    assert len(queue.json()["appointments"]) == 1
    assert queue.json()["appointments"][0]["id"] == appointment["id"]
    assert "note" not in queue.json()["appointments"][0]

    confirmed = client.post(
        f"/v1/dental/assistant/appointments/{appointment['id']}/confirm",
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    cancelled = client.post(
        f"/v1/dental/assistant/appointments/{appointment['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    audit_actions = {
        row["action"]
        for row in store._execute(
            "SELECT action FROM dental_audit_events WHERE actor_id=?",
            (assistant["id"],),
        )
    }
    assert {
        "assistant.patient_directory.viewed",
        "assistant.patient_contact.viewed",
        "assistant.patient_intake.created",
        "assistant.appointment_request.created",
        "assistant.appointment.confirmed",
        "assistant.appointment.cancelled",
    }.issubset(audit_actions)


def test_assistant_billing_access_is_read_only_and_redacted(assistant_api):
    client, store = assistant_api
    _create_assistant(client)
    headers, _login = _assistant_headers(client)
    patient = _patient(store, "billing-patient", "clinic-a")
    invoice = store.create_invoice(
        {
            "patient_id": patient["id"],
            "idempotency_key": "assistant-test-invoice-001",
            "items": [
                {
                    "description": "Synthetic billing item",
                    "quantity_milli": 1000,
                    "unit_price_paise": 15000,
                    "tax_rate_bps": 0,
                }
            ],
            "discount_paise": 0,
            "patient_note": "private invoice note must not be exposed",
        },
        "clinic-a",
        "doctor-owner",
    )
    store.issue_invoice(invoice["id"], "clinic-a", "doctor-owner")
    store.record_invoice_receipt(
        invoice["id"],
        "clinic-a",
        "doctor-owner",
        {
            "amount_paise": 5000,
            "method": "upi",
            "reference": "SYNTHETIC-REF",
            "idempotency_key": "assistant-test-receipt-001",
            "paid_at": None,
        },
    )

    response = client.get(
        f"/v1/dental/assistant/patients/{patient['id']}/billing",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()["invoices"][0]
    assert result["invoice_number"] == invoice["invoice_number"]
    assert result["total_paise"] == 15000
    assert result["paid_paise"] == 5000
    assert result["balance_paise"] == 10000
    assert result["receipts"][0]["amount_paise"] == 5000
    assert "items" not in result
    assert "patient_note" not in result
    assert "description" not in result

    denied_write = client.post(
        f"/v1/dental/doctor/invoices/{invoice['id']}/receipts",
        headers=headers,
        json={
            "amount_paise": 1000,
            "method": "cash",
            "idempotency_key": "assistant-denied-receipt-001",
        },
    )
    assert denied_write.status_code == 403


def test_assistant_gets_403_for_doctor_clinical_ai_and_owner_operations(assistant_api):
    client, store = assistant_api
    _create_assistant(client)
    headers, _login = _assistant_headers(client)
    patient = _patient(store, "restricted-patient", "clinic-a")

    denied_requests = [
        ("GET", "/v1/dental/patients", None),
        ("GET", f"/v1/dental/patients/{patient['id']}/chart", None),
        ("GET", f"/v1/dental/doctor/patients/{patient['id']}/clinical-notes", None),
        ("POST", f"/v1/dental/doctor/patients/{patient['id']}/treatment-plans", {
            "title": "Synthetic plan",
            "items": [{"description": "Not permitted"}],
        }),
        ("GET", f"/v1/dental/doctor/patients/{patient['id']}/invoices", None),
        ("GET", f"/v1/dental/doctor/patients/{patient['id']}/documents", None),
        ("POST", "/v1/dental/intelligence/run", {"goal": "Do not execute clinical AI"}),
        ("POST", "/v1/dental/scans/screen", {"scan_reference": "synthetic"}),
        ("POST", "/v1/dental/organisations", {
            "name": "Not Allowed",
            "organisation_type": "clinic",
        }),
        ("POST", "/v1/dental/auth/assistants", {
            "email": "second-assistant@example.test",
            "password": "another-assistant-password",
        }),
    ]
    for method, path, payload in denied_requests:
        response = client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, (
            f"{method} {path} returned {response.status_code}: {response.text}"
        )

    assert store._execute(
        "SELECT id FROM dental_clinic_assistants WHERE email=?",
        ("second-assistant@example.test",),
    ) == []


def test_assistant_cannot_cross_clinic_tenant_boundary(assistant_api):
    client, store = assistant_api
    _create_assistant(client)
    headers, _login = _assistant_headers(client)
    foreign_patient = _patient(store, "foreign-patient", "clinic-b")

    foreign_profile = client.get(
        f"/v1/dental/assistant/patients/{foreign_patient['id']}",
        headers=headers,
    )
    assert foreign_profile.status_code == 404

    foreign_appointment = client.post(
        "/v1/dental/assistant/appointments",
        headers=headers,
        json={
            "patient_id": foreign_patient["id"],
            "starts_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            "treatment_type": "Dental check-up",
        },
    )
    assert foreign_appointment.status_code == 404

    directory = client.get("/v1/dental/assistant/patients", headers=headers)
    assert directory.status_code == 200
    assert all(
        patient["id"] != foreign_patient["id"]
        for patient in directory.json()["patients"]
    )


def test_assistant_deactivation_revokes_existing_sessions(assistant_api):
    client, _store = assistant_api
    created = _create_assistant(client)
    headers, _login = _assistant_headers(client)

    deactivated = client.post(
        f"/v1/dental/auth/assistants/{created['id']}/deactivate",
        headers=_owner_headers(client),
    )
    assert deactivated.status_code == 200

    existing_session = client.get("/v1/dental/assistant/patients", headers=headers)
    assert existing_session.status_code == 401

    new_login = client.post(
        "/v1/dental/auth/assistant/login",
        json={"email": ASSISTANT_EMAIL, "password": ASSISTANT_PASSWORD},
    )
    assert new_login.status_code == 401


def test_assistant_routes_require_auth_and_doctor_cannot_use_assistant_session(assistant_api):
    client, _store = assistant_api
    unauthenticated = client.get("/v1/dental/assistant/patients")
    assert unauthenticated.status_code == 401

    doctor_headers = _owner_headers(client)
    doctor_on_assistant_route = client.get(
        "/v1/dental/assistant/patients", headers=doctor_headers
    )
    assert doctor_on_assistant_route.status_code == 401


def test_non_admin_doctor_cannot_create_or_deactivate_assistant(assistant_api):
    client, store = assistant_api
    store.create_doctor(
        {
            "id": "doctor-member",
            "email": "doctor-member@example.test",
            "password_hash": dental_auth.hash_password("doctor-member-password"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-member", "clinic-a", "doctor")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "doctor-member@example.test",
            "password": "doctor-member-password",
        },
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    denied = client.post(
        "/v1/dental/auth/assistants",
        headers=headers,
        json={"email": "not-admin@example.test", "password": "not-admin-password"},
    )
    assert denied.status_code == 403
    denied_list = client.get("/v1/dental/auth/assistants", headers=headers)
    assert denied_list.status_code == 403
