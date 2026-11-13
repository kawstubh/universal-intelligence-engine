from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_assistant, dental_store
from universal_intelligence_engine.api import app
from universal_intelligence_engine.dental_auth import hash_password
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def assistant_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "assistant.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    monkeypatch.setattr(dental_store, "_store", None)
    dental_assistant._LOGIN_ATTEMPTS.clear()
    store = DentalStore()
    store.create_organisation(
        {"id": "org-assistant-a", "name": "Assistant Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {"id": "clinic-assistant-a", "organisation_id": "org-assistant-a", "name": "Main Clinic", "clinic_type": "clinic"}
    )
    store.create_organisation(
        {"id": "org-assistant-b", "name": "Other Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {"id": "clinic-assistant-b", "organisation_id": "org-assistant-b", "name": "Other Clinic", "clinic_type": "clinic"}
    )
    store.create_doctor(
        {
            "id": "doctor-assistant-owner",
            "email": "owner@example.test",
            "password_hash": hash_password("OwnerPassword!123"),
            "role": "admin",
            "clinic_id": "clinic-assistant-a",
        }
    )
    store.create_doctor_membership(
        "doctor-assistant-owner", "clinic-assistant-a", "owner"
    )
    patient_a = store.create_patient(
        {"name": "Synthetic Patient A", "phone": "9000000001", "age": "35"},
        "clinic-assistant-a",
    )
    patient_b = store.create_patient(
        {"name": "Synthetic Patient B", "phone": "9000000002", "age": "29"},
        "clinic-assistant-b",
    )
    starts_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    appointment_a = store.create_appointment(
        {
            "patient_id": patient_a["id"],
            "starts_at": starts_at,
            "treatment_type": "Dental check-up",
            "note": "Private clinical note must not be returned",
            "status": "requested",
        },
        "clinic-assistant-a",
    )
    appointment_b = store.create_appointment(
        {
            "patient_id": patient_b["id"],
            "starts_at": starts_at,
            "treatment_type": "Cleaning",
            "note": "Other clinic private note",
            "status": "requested",
        },
        "clinic-assistant-b",
    )
    invoice_a = store.create_invoice(
        {
            "patient_id": patient_a["id"],
            "idempotency_key": "assistant-invoice-key-001",
            "items": [
                {
                    "description": "Sensitive clinical treatment description",
                    "quantity_milli": 1000,
                    "unit_price_paise": 10000,
                    "tax_rate_bps": 0,
                }
            ],
            "discount_paise": 0,
            "patient_note": "Private clinical/billing note",
        },
        "clinic-assistant-a",
        "doctor-assistant-owner",
    )
    store.issue_invoice(
        invoice_a["id"], "clinic-assistant-a", "doctor-assistant-owner", None
    )
    invoice_b = store.create_invoice(
        {
            "patient_id": patient_b["id"],
            "idempotency_key": "assistant-invoice-key-002",
            "items": [
                {
                    "description": "Other clinic treatment",
                    "quantity_milli": 1000,
                    "unit_price_paise": 5000,
                    "tax_rate_bps": 0,
                }
            ],
            "discount_paise": 0,
        },
        "clinic-assistant-b",
        "doctor-assistant-owner",
    )
    store.issue_invoice(
        invoice_b["id"], "clinic-assistant-b", "doctor-assistant-owner", None
    )
    client = TestClient(app)
    owner_login = client.post(
        "/v1/dental/auth/password/login",
        json={"email": "owner@example.test", "password": "OwnerPassword!123"},
    )
    assert owner_login.status_code == 200, owner_login.text
    owner_token = owner_login.json()["access_token"]
    created = client.post(
        "/v1/dental/clinic-assistants",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "reception@example.test", "password": "ReceptionPassword!123"},
    )
    assert created.status_code == 201, created.text
    assistant_id = created.json()["result"]["id"]
    assistant_login = client.post(
        "/v1/dental/assistant/auth/login",
        json={"email": "reception@example.test", "password": "ReceptionPassword!123"},
    )
    assert assistant_login.status_code == 200, assistant_login.text
    assistant_token = assistant_login.json()["access_token"]
    return {
        "client": client,
        "store": store,
        "owner_token": owner_token,
        "assistant_token": assistant_token,
        "assistant_id": assistant_id,
        "patient_a": patient_a,
        "patient_b": patient_b,
        "appointment_a": appointment_a,
        "appointment_b": appointment_b,
        "invoice_a": invoice_a,
        "invoice_b": invoice_b,
    }


def _assistant_headers(context):
    return {"Authorization": f"Bearer {context['assistant_token']}"}


def _owner_headers(context):
    return {"Authorization": f"Bearer {context['owner_token']}"}


def test_assistant_has_a_distinct_identity_and_can_only_use_reception_and_billing_api(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)

    me = client.get("/v1/dental/assistant/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "assistant"
    assert me.json()["user"]["id"] == context["assistant_id"]

    patients = client.get("/v1/dental/assistant/patients", headers=headers)
    assert patients.status_code == 200
    assert len(patients.json()["result"]) == 1
    patient = patients.json()["result"][0]
    assert patient["name"] == "Synthetic Patient A"
    assert patient["phone"] == "9000000001"
    assert "id" not in patient
    assert "age" not in patient
    assert "clinic_id" not in patient

    created_patient = client.post(
        "/v1/dental/assistant/patients",
        headers=headers,
        json={"name": "Reception Intake", "phone": "9000000010"},
    )
    assert created_patient.status_code == 201, created_patient.text
    assert "age" not in created_patient.json()["result"]
    assert "id" not in created_patient.json()["result"]

    appointments = client.get("/v1/dental/assistant/appointments", headers=headers)
    assert appointments.status_code == 200
    assert len(appointments.json()["result"]) == 1
    appointment = appointments.json()["result"][0]
    assert appointment["patient_name"] == "Synthetic Patient A"
    assert appointment["status"] == "requested"
    assert "note" not in appointment
    assert "patient_id" not in appointment

    confirmed = client.patch(
        f"/v1/dental/assistant/appointments/{context['appointment_a']['id']}",
        headers=headers,
        json={"status": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["result"]["status"] == "confirmed"
    assert "patient_id" not in confirmed.json()["result"]

    missing_reschedule_time = client.patch(
        f"/v1/dental/assistant/appointments/{context['appointment_a']['id']}",
        headers=headers,
        json={"status": "rescheduled"},
    )
    assert missing_reschedule_time.status_code == 422
    naive_reschedule_time = client.patch(
        f"/v1/dental/assistant/appointments/{context['appointment_a']['id']}",
        headers=headers,
        json={"starts_at": "2026-11-20T10:00:00"},
    )
    assert naive_reschedule_time.status_code == 422

    invalid_intake = client.post(
        "/v1/dental/assistant/patients",
        headers=headers,
        json={"name": "Invalid Contact", "phone": "1234567890"},
    )
    assert invalid_intake.status_code == 422

    invoices = client.get("/v1/dental/assistant/billing/invoices", headers=headers)
    assert invoices.status_code == 200
    assert len(invoices.json()["result"]) == 1
    invoice_summary = invoices.json()["result"][0]
    assert invoice_summary["patient_name"] == "Synthetic Patient A"
    assert invoice_summary["balance_paise"] == 10000
    assert "items" not in invoice_summary
    assert "patient_note" not in invoice_summary
    assert "patient_id" not in invoice_summary

    receipt = client.post(
        f"/v1/dental/assistant/billing/invoices/{context['invoice_a']['id']}/receipts",
        headers=headers,
        json={
            "amount_paise": 2500,
            "method": "upi",
            "reference": "SYNTHETIC-UPI-REF",
            "idempotency_key": "assistant-receipt-key-001",
        },
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["result"]["amount_paise"] == 2500
    persisted = context["store"].get_invoice(
        context["invoice_a"]["id"], "clinic-assistant-a"
    )
    assert persisted["paid_paise"] == 2500
    assert persisted["status"] == "partially_paid"
    receipt_row = context["store"]._execute(
        "SELECT created_by_doctor_id,created_by_assistant_id FROM dental_receipts WHERE invoice_id=?",
        (context["invoice_a"]["id"],),
    )[0]
    assert receipt_row["created_by_doctor_id"] is None
    assert receipt_row["created_by_assistant_id"] == context["assistant_id"]


def test_assistant_receipt_idempotency_and_overpayment_are_enforced(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)
    path = f"/v1/dental/assistant/billing/invoices/{context['invoice_a']['id']}/receipts"
    payload = {
        "amount_paise": 1000,
        "method": "cash",
        "idempotency_key": "assistant-receipt-key-002",
    }
    first = client.post(path, headers=headers, json=payload)
    second = client.post(path, headers=headers, json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["result"]["id"] == second.json()["result"]["id"]

    overpayment = client.post(
        path,
        headers=headers,
        json={
            "amount_paise": 999999,
            "method": "cash",
            "idempotency_key": "assistant-receipt-key-003",
        },
    )
    assert overpayment.status_code == 409
    assert overpayment.json()["detail"]["code"] == "RECEIPT_EXCEEDS_BALANCE"


def test_assistant_is_denied_all_doctor_clinical_and_owner_routes_with_403(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)
    patient_id = context["patient_a"]["id"]
    denied = [
        ("GET", "/v1/dental/patients"),
        ("GET", "/v1/dental/appointments"),
        ("GET", f"/v1/dental/patients/{patient_id}/chart"),
        ("GET", f"/v1/dental/patients/{patient_id}/periodontogram"),
        ("POST", "/v1/dental/intelligence/run"),
        ("GET", f"/v1/dental/doctor/patients/{patient_id}/clinical-notes"),
        ("POST", f"/v1/dental/doctor/patients/{patient_id}/treatment-plans"),
        ("GET", f"/v1/dental/doctor/patients/{patient_id}/invoices"),
        ("POST", "/v1/dental/organisations"),
        ("GET", "/v1/dental/auth/workspaces"),
    ]
    bodies = {
        ("POST", "/v1/dental/intelligence/run"): {"goal": "Summarise clinical findings"},
        ("POST", f"/v1/dental/doctor/patients/{patient_id}/treatment-plans"): {
            "title": "Not allowed",
            "items": [{"description": "Not allowed"}],
        },
        ("POST", "/v1/dental/organisations"): {
            "name": "Not allowed",
            "organisation_type": "clinic",
        },
    }
    for method, path in denied:
        response = client.request(
            method,
            path,
            headers=headers,
            json=bodies.get((method, path)),
        )
        assert response.status_code == 403, (method, path, response.status_code, response.text)


def test_assistant_tenant_isolation_and_cross_clinic_account_management(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)

    patients = client.get("/v1/dental/assistant/patients", headers=headers).json()["result"]
    assert {item["name"] for item in patients} == {"Synthetic Patient A"}

    appointments = client.get("/v1/dental/assistant/appointments", headers=headers).json()["result"]
    assert {item["id"] for item in appointments} == {context["appointment_a"]["id"]}
    assert "patient_id" not in appointments[0]

    invoices = client.get("/v1/dental/assistant/billing/invoices", headers=headers).json()["result"]
    assert {item["id"] for item in invoices} == {context["invoice_a"]["id"]}

    cross_appointment = client.patch(
        f"/v1/dental/assistant/appointments/{context['appointment_b']['id']}",
        headers=headers,
        json={"status": "confirmed"},
    )
    assert cross_appointment.status_code == 404

    cross_receipt = client.post(
        f"/v1/dental/assistant/billing/invoices/{context['invoice_b']['id']}/receipts",
        headers=headers,
        json={
            "amount_paise": 1000,
            "method": "cash",
            "idempotency_key": "assistant-cross-tenant-001",
        },
    )
    assert cross_receipt.status_code == 404

    owner = _owner_headers(context)
    cross_account = client.patch(
        f"/v1/dental/clinic-assistants/{context['assistant_id']}",
        headers=owner,
        json={"active": False},
    )
    assert cross_account.status_code == 200
    revoked = client.get("/v1/dental/assistant/me", headers=headers)
    assert revoked.status_code == 401


def test_doctor_token_cannot_be_used_as_an_assistant_token(assistant_api):
    context = assistant_api
    response = context["client"].get(
        "/v1/dental/assistant/patients",
        headers=_owner_headers(context),
    )
    assert response.status_code == 401


def test_assistant_cannot_reuse_a_revoked_session_after_logout(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)
    logout = client.post("/v1/dental/assistant/auth/logout", headers=headers)
    assert logout.status_code == 200
    denied = client.get("/v1/dental/assistant/me", headers=headers)
    assert denied.status_code == 401


def test_assistant_cannot_read_private_care_documents_messages_or_owner_billing(assistant_api):
    context = assistant_api
    client = context["client"]
    headers = _assistant_headers(context)
    patient_id = context["patient_a"]["id"]

    denied_gets = [
        f"/v1/dental/doctor/patients/{patient_id}/clinical-notes",
        f"/v1/dental/doctor/patients/{patient_id}/treatment-plans",
        f"/v1/dental/doctor/patients/{patient_id}/documents",
        f"/v1/dental/doctor/patients/{patient_id}/messages",
        "/v1/dental/billing/membership",
        "/v1/dental/auth/workspaces",
        "/v1/dental/organisations/org-assistant-a/workspaces",
    ]
    for path in denied_gets:
        response = client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.status_code, response.text)

    invite = client.post(
        f"/v1/dental/doctor/patients/{patient_id}/invite",
        headers=headers,
    )
    assert invite.status_code == 403, invite.text

    workspace_switch = client.post(
        "/v1/dental/auth/workspace/switch",
        headers=headers,
        json={"clinic_id": "clinic-assistant-b"},
    )
    assert workspace_switch.status_code == 403, workspace_switch.text

    membership_create = client.post(
        "/v1/dental/workspaces/clinic-assistant-a/memberships",
        headers=headers,
        json={"doctor_id": "doctor-assistant-owner", "role": "doctor"},
    )
    assert membership_create.status_code == 403, membership_create.text

    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert not any("prescription" in path.lower() for path in openapi_paths), (
        "No prescription signing route should be exposed in this release."
    )


def test_non_admin_doctor_cannot_manage_clinic_assistants(assistant_api):
    context = assistant_api
    client = context["client"]
    store = context["store"]
    store.create_doctor(
        {
            "id": "doctor-assistant-staff",
            "email": "staff@example.test",
            "password_hash": hash_password("StaffPassword!123"),
            "role": "doctor",
            "clinic_id": "clinic-assistant-a",
        }
    )
    store.create_doctor_membership(
        "doctor-assistant-staff", "clinic-assistant-a", "doctor"
    )
    login = client.post(
        "/v1/dental/auth/password/login",
        json={"email": "staff@example.test", "password": "StaffPassword!123"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    listed = client.get("/v1/dental/clinic-assistants", headers=headers)
    assert listed.status_code == 403, listed.text
    created = client.post(
        "/v1/dental/clinic-assistants",
        headers=headers,
        json={"email": "not-allowed@example.test", "password": "NotAllowedPassword!123"},
    )
    assert created.status_code == 403, created.text


def test_clinic_assistant_account_management_is_tenant_scoped(assistant_api):
    context = assistant_api
    client = context["client"]
    store = context["store"]

    store.create_doctor(
        {
            "id": "doctor-assistant-owner-b",
            "email": "owner-b@example.test",
            "password_hash": hash_password("OwnerBPassword!123"),
            "role": "admin",
            "clinic_id": "clinic-assistant-b",
        }
    )
    store.create_doctor_membership(
        "doctor-assistant-owner-b", "clinic-assistant-b", "owner"
    )
    owner_b_login = client.post(
        "/v1/dental/auth/password/login",
        json={"email": "owner-b@example.test", "password": "OwnerBPassword!123"},
    )
    assert owner_b_login.status_code == 200, owner_b_login.text
    owner_b_headers = {
        "Authorization": f"Bearer {owner_b_login.json()['access_token']}"
    }
    created = client.post(
        "/v1/dental/clinic-assistants",
        headers=owner_b_headers,
        json={"email": "reception-b@example.test", "password": "ReceptionBPassword!123"},
    )
    assert created.status_code == 201, created.text
    assistant_b_id = created.json()["result"]["id"]

    owner_a_list = client.get(
        "/v1/dental/clinic-assistants",
        headers=_owner_headers(context),
    )
    assert owner_a_list.status_code == 200, owner_a_list.text
    assert assistant_b_id not in {
        item["id"] for item in owner_a_list.json()["result"]
    }

    cross_tenant_deactivation = client.patch(
        f"/v1/dental/clinic-assistants/{assistant_b_id}",
        headers=_owner_headers(context),
        json={"active": False},
    )
    assert cross_tenant_deactivation.status_code == 404, cross_tenant_deactivation.text
