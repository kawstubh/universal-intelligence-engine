from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_patient_care
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def care_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "patient-care.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    store.create_organisation({"id": "org-a", "name": "Clinic Group", "organisation_type": "group"})
    store.create_organisation({"id": "org-b", "name": "Other Clinic", "organisation_type": "clinic"})
    store.create_clinic({"id": "clinic-a", "organisation_id": "org-a", "name": "Main Clinic", "clinic_type": "clinic"})
    store.create_clinic({"id": "clinic-b", "organisation_id": "org-b", "name": "Other Clinic", "clinic_type": "clinic"})
    patient_a = store.create_patient({"name": "Synthetic Patient A", "phone": "9000000001", "age": "35"}, "clinic-a")
    patient_b = store.create_patient({"name": "Synthetic Patient B", "phone": "9000000002", "age": "29"}, "clinic-b")
    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    patient_actor = {}
    monkeypatch.setattr(dental_patient_care, "get_dental_store", lambda: store)

    app = FastAPI()
    app.include_router(dental_patient_care.router)
    app.dependency_overrides[dental_patient_care.require_doctor] = lambda: actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b


def test_treatment_plan_remains_clinic_only_and_is_workspace_scoped(care_api):
    client, _store, actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={
            "title": "Initial care plan",
            "summary": "A proposed care sequence for the clinic record.",
            "items": [
                {"service_code": "EXAM", "description": "Clinical examination", "tooth_fdi": "16"},
                {"service_code": "CLEAN", "description": "Professional cleaning"},
            ],
        },
    )
    assert created.status_code == 200, created.text
    plan = created.json()["result"]
    assert plan["status"] == "draft"
    assert plan["patient_visible"] is False
    assert len(plan["items"]) == 2
    assert client.get("/v1/dental/patient/treatment-plans").status_code == 404

    actor["clinic_id"] = "clinic-b"
    cross_workspace = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"status": "proposed"},
    )
    assert cross_workspace.status_code == 404

    actor["clinic_id"] = "clinic-a"
    proposed = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"status": "proposed"},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["result"]["patient_visible"] is False
    assert client.get("/v1/dental/patient/treatment-plans").status_code == 404


def test_treatment_plan_patient_visibility_cannot_be_enabled(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={"title": "Private draft", "items": [{"description": "Assessment"}]},
    )
    plan_id = created.json()["result"]["id"]
    response = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan_id}",
        json={"patient_visible": True},
    )
    assert response.status_code == 422


def test_invoice_uses_integer_paise_pre_tax_discount_and_remains_clinic_only(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={
            "idempotency_key": "invoice-test-key-0001",
            "discount_paise": 1000,
            "patient_note": "Please contact the clinic if you have a billing question.",
            "items": [
                {
                    "description": "Dental procedure",
                    "quantity_milli": 1500,
                    "unit_price_paise": 10000,
                    "tax_rate_bps": 1800,
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    invoice = created.json()["result"]
    assert invoice["status"] == "draft"
    assert invoice["patient_visible"] in (False, 0)
    assert invoice["subtotal_paise"] == 15000
    assert invoice["discount_paise"] == 1000
    assert invoice["tax_paise"] == 2520
    assert invoice["total_paise"] == 16520
    assert invoice["items"][0]["discount_paise"] == 1000
    assert invoice["items"][0]["total_paise"] == 16520

    assert client.get("/v1/dental/patient/invoices").status_code == 404

    issued = client.post(
        f"/v1/dental/doctor/invoices/{invoice['id']}/issue",
        json={"due_date": "2026-12-01"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["result"]["status"] == "issued"
    assert issued.json()["result"]["patient_visible"] is False
    assert client.get("/v1/dental/patient/invoices").status_code == 404


def test_receipt_idempotency_partial_payment_and_overpayment_guard(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={
            "idempotency_key": "invoice-test-key-0002",
            "items": [
                {"description": "Dental treatment", "quantity_milli": 1000, "unit_price_paise": 20000}
            ]
        },
    )
    invoice_id = created.json()["result"]["id"]
    assert client.post(f"/v1/dental/doctor/invoices/{invoice_id}/issue", json={}).status_code == 200

    receipt_body = {
        "amount_paise": 5000,
        "method": "upi",
        "reference": "UPI-TEST-001",
        "idempotency_key": "receipt-test-key-0001",
    }
    first = client.post(f"/v1/dental/doctor/invoices/{invoice_id}/receipts", json=receipt_body)
    assert first.status_code == 200, first.text
    duplicate = client.post(f"/v1/dental/doctor/invoices/{invoice_id}/receipts", json=receipt_body)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["result"]["id"] == first.json()["result"]["id"]

    doctor_invoice = client.get(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices"
    ).json()["result"][0]
    assert doctor_invoice["paid_paise"] == 5000
    assert doctor_invoice["balance_paise"] == 15000
    assert doctor_invoice["status"] == "partially_paid"

    second = client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/receipts",
        json={
            "amount_paise": 15000,
            "method": "cash",
            "reference": "CASH-TEST-002",
            "idempotency_key": "receipt-test-key-0002",
        },
    )
    assert second.status_code == 200, second.text
    paid = client.get(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices"
    ).json()["result"][0]
    assert paid["status"] == "paid"
    assert paid["paid_paise"] == 20000
    assert paid["balance_paise"] == 0

    assert client.get("/v1/dental/patient/receipts").status_code == 404

    overpayment = client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/receipts",
        json={
            "amount_paise": 1,
            "method": "cash",
            "idempotency_key": "receipt-test-key-0003",
        },
    )
    assert overpayment.status_code == 409
    assert overpayment.json()["detail"]["code"] == "DENTAL_RECORD_CONFLICT"


def test_invoice_isolation_and_patient_account_routes_are_absent(care_api):
    client, _store, actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={"idempotency_key": "invoice-test-key-0003", "items": [{"description": "Check-up", "quantity_milli": 1000, "unit_price_paise": 5000}]},
    )
    invoice_id = created.json()["result"]["id"]
    client.post(f"/v1/dental/doctor/invoices/{invoice_id}/issue", json={})

    actor["clinic_id"] = "clinic-b"
    denied = client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/void",
    )
    assert denied.status_code == 404

    assert client.get("/v1/dental/patient/invoices").status_code == 404
    assert client.get("/v1/dental/patient/receipts").status_code == 404


def test_invoice_cannot_be_voided_after_receipt(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={"idempotency_key": "invoice-test-key-0004", "items": [{"description": "Cleaning", "quantity_milli": 1000, "unit_price_paise": 10000}]},
    )
    invoice_id = created.json()["result"]["id"]
    client.post(f"/v1/dental/doctor/invoices/{invoice_id}/issue", json={})
    client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/receipts",
        json={
            "amount_paise": 1000,
            "method": "cash",
            "idempotency_key": "receipt-test-key-0004",
        },
    )
    voided = client.post(f"/v1/dental/doctor/invoices/{invoice_id}/void")
    assert voided.status_code == 409


def test_doctor_routes_require_auth_and_patient_account_routes_are_absent():
    app = FastAPI()
    app.include_router(dental_patient_care.router)
    client = TestClient(app)
    assert client.get("/v1/dental/patient/invoices").status_code == 404
    assert client.get("/v1/dental/doctor/patients/missing/invoices").status_code == 401


def test_invoice_validation_rejects_discount_larger_than_subtotal(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    response = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={
            "idempotency_key": "invoice-test-key-0005",
            "discount_paise": 5001,
            "items": [{"description": "Service", "quantity_milli": 1000, "unit_price_paise": 5000}],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DENTAL_RECORD_INVALID"


def test_treatment_plan_items_are_editable_only_while_draft_with_version_control(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={"title": "Draft plan", "items": [{"description": "Initial assessment"}]},
    )
    plan = created.json()["result"]
    assert plan["version"] == 1

    updated = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={
            "expected_version": 1,
            "title": "Revised draft",
            "items": [
                {"description": "Clinical assessment", "tooth_fdi": "16"},
                {"description": "Professional cleaning"},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    revised = updated.json()["result"]
    assert revised["version"] == 2
    assert revised["title"] == "Revised draft"
    assert len(revised["items"]) == 2

    conflict = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"expected_version": 1, "summary": "Stale update"},
    )
    assert conflict.status_code == 409

    proposed = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"expected_version": 2, "status": "proposed"},
    )
    assert proposed.status_code == 200, proposed.text

    frozen_items = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"expected_version": 3, "items": [{"description": "Late edit"}]},
    )
    assert frozen_items.status_code == 409

    invalid_transition = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"expected_version": 3, "status": "completed"},
    )
    assert invalid_transition.status_code == 409


def test_invoice_creation_is_idempotent_and_rejects_key_reuse_with_different_payload(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    body = {
        "idempotency_key": "invoice-create-key-0001",
        "items": [{"description": "Consultation", "quantity_milli": 1000, "unit_price_paise": 7500}],
    }
    first = client.post(f"/v1/dental/doctor/patients/{patient_a['id']}/invoices", json=body)
    assert first.status_code == 200, first.text
    duplicate = client.post(f"/v1/dental/doctor/patients/{patient_a['id']}/invoices", json=body)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["result"]["id"] == first.json()["result"]["id"]
    assert len(client.get(f"/v1/dental/doctor/patients/{patient_a['id']}/invoices").json()["result"]) == 1

    changed = {
        **body,
        "items": [{"description": "Different service", "quantity_milli": 1000, "unit_price_paise": 9000}],
    }
    conflict = client.post(f"/v1/dental/doctor/patients/{patient_a['id']}/invoices", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "DENTAL_RECORD_CONFLICT"


def test_doctor_dashboard_summary_is_live_and_scoped_to_active_workspace(care_api):
    client, store, actor, _patient_actor, patient_a, _patient_b = care_api

    plan = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={
            "title": "Dashboard treatment plan",
            "items": [{"description": "Clinical review"}],
        },
    )
    assert plan.status_code == 200, plan.text
    plan_id = plan.json()["result"]["id"]
    proposed = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan_id}",
        json={"status": "proposed"},
    )
    assert proposed.status_code == 200, proposed.text

    invoice = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invoices",
        json={
            "idempotency_key": "dashboard-summary-invoice-0001",
            "items": [
                {
                    "description": "Examination",
                    "quantity_milli": 1000,
                    "unit_price_paise": 100000,
                    "tax_rate_bps": 0,
                }
            ],
        },
    )
    assert invoice.status_code == 200, invoice.text
    invoice_id = invoice.json()["result"]["id"]
    issued = client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/issue",
        json={},
    )
    assert issued.status_code == 200, issued.text
    receipt = client.post(
        f"/v1/dental/doctor/invoices/{invoice_id}/receipts",
        json={
            "amount_paise": 25000,
            "method": "upi",
            "idempotency_key": "dashboard-summary-receipt-0001",
        },
    )
    assert receipt.status_code == 200, receipt.text

    store.create_appointment(
        {
            "patient_id": patient_a["id"],
            "starts_at": "2026-11-12T10:00:00+00:00",
            "treatment_type": "Check-up",
            "status": "requested",
        },
        "clinic-a",
    )

    response = client.get("/v1/dental/doctor/dashboard/summary")
    assert response.status_code == 200, response.text
    summary = response.json()["result"]
    assert summary == {
        "patient_count": 1,
        "appointment_count": 1,
        "pending_appointment_requests": 1,
        "active_treatment_plan_count": 1,
        "issued_invoice_count": 1,
        "invoiced_total_paise": 100000,
        "collected_total_paise": 25000,
        "outstanding_balance_paise": 75000,
    }
    assert "patient_id" not in summary
    assert "clinic_id" not in summary
    assert "patient_name" not in summary

    actor["clinic_id"] = "clinic-b"
    other_workspace = client.get("/v1/dental/doctor/dashboard/summary")
    assert other_workspace.status_code == 200
    assert other_workspace.json()["result"] == {
        "patient_count": 1,
        "appointment_count": 0,
        "pending_appointment_requests": 0,
        "active_treatment_plan_count": 0,
        "issued_invoice_count": 0,
        "invoiced_total_paise": 0,
        "collected_total_paise": 0,
        "outstanding_balance_paise": 0,
    }


def test_doctor_dashboard_summary_returns_zeroes_for_an_empty_workspace(care_api):
    client, store, actor, _patient_actor, _patient_a, _patient_b = care_api
    store.create_organisation(
        {"id": "org-empty", "name": "Empty Clinic Group", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-empty",
            "organisation_id": "org-empty",
            "name": "Empty Workspace",
            "clinic_type": "clinic",
        }
    )
    actor["clinic_id"] = "clinic-empty"
    response = client.get("/v1/dental/doctor/dashboard/summary")
    assert response.status_code == 200
    assert response.json()["result"] == {
        "patient_count": 0,
        "appointment_count": 0,
        "pending_appointment_requests": 0,
        "active_treatment_plan_count": 0,
        "issued_invoice_count": 0,
        "invoiced_total_paise": 0,
        "collected_total_paise": 0,
        "outstanding_balance_paise": 0,
    }


def test_doctor_dashboard_summary_requires_doctor_authentication():
    app = FastAPI()
    app.include_router(dental_patient_care.router)
    client = TestClient(app)
    response = client.get("/v1/dental/doctor/dashboard/summary")
    assert response.status_code == 401


def test_patient_account_routes_are_not_mounted_on_care_router():
    app = FastAPI()
    app.include_router(dental_patient_care.router)
    client = TestClient(app)
    for path in (
        "/v1/dental/patient/linked-records",
        "/v1/dental/patient/treatment-plans",
        "/v1/dental/patient/invoices",
        "/v1/dental/patient/receipts",
        "/v1/dental/patient/appointments",
        "/v1/dental/patient/chart",
    ):
        assert client.get(path).status_code == 404


def test_doctor_patient_invite_route_is_disabled_for_account_free_journey(care_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b = care_api
    response = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/invite"
    )
    assert response.status_code == 404
