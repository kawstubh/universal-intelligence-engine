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
    store._execute(
        "UPDATE dental_patients SET user_id=? WHERE id=?",
        ("supabase-user-a", patient_a["id"]),
    )
    store._execute(
        "UPDATE dental_patients SET user_id=? WHERE id=?",
        ("supabase-user-b", patient_b["id"]),
    )

    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    patient_actor = {"user_id": "supabase-user-a"}
    monkeypatch.setattr(dental_patient_care, "get_dental_store", lambda: store)

    app = FastAPI()
    app.include_router(dental_patient_care.router)
    app.dependency_overrides[dental_patient_care.require_doctor] = lambda: actor
    app.dependency_overrides[dental_patient_care.require_patient] = lambda: patient_actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b


def test_treatment_plan_is_private_until_doctor_shares_it_and_is_workspace_scoped(care_api):
    client, _store, actor, patient_actor, patient_a, _patient_b = care_api
    created = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={
            "title": "Initial care plan",
            "summary": "A proposed care sequence to discuss with the patient.",
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

    hidden = client.get("/v1/dental/patient/treatment-plans")
    assert hidden.status_code == 200
    assert hidden.json()["result"] == []

    actor["clinic_id"] = "clinic-b"
    cross_workspace = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"status": "proposed", "patient_visible": True},
    )
    assert cross_workspace.status_code == 404

    actor["clinic_id"] = "clinic-a"
    shared = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan['id']}",
        json={"status": "proposed", "patient_visible": True},
    )
    assert shared.status_code == 200, shared.text
    visible = client.get("/v1/dental/patient/treatment-plans")
    assert visible.status_code == 200
    assert len(visible.json()["result"]) == 1
    patient_plan = visible.json()["result"][0]
    assert patient_plan["id"] == plan["id"]
    assert patient_plan["title"] == "Initial care plan"
    assert len(patient_plan["items"]) == 2
    assert "clinic_id" not in patient_plan
    assert "patient_id" not in patient_plan
    assert "created_by_doctor_id" not in patient_plan

    patient_actor["user_id"] = "supabase-user-b"
    other_patient = client.get("/v1/dental/patient/treatment-plans")
    assert other_patient.status_code == 200
    assert other_patient.json()["result"] == []


def test_treatment_plan_draft_cannot_be_shared(care_api):
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
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DENTAL_RECORD_CONFLICT"


def test_invoice_uses_integer_paise_pre_tax_discount_and_patient_visibility(care_api):
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

    assert client.get("/v1/dental/patient/invoices").json()["result"] == []

    issued = client.post(
        f"/v1/dental/doctor/invoices/{invoice['id']}/issue",
        json={"due_date": "2026-12-01"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["result"]["status"] == "issued"
    patient_invoices = client.get("/v1/dental/patient/invoices")
    assert patient_invoices.status_code == 200
    visible = patient_invoices.json()["result"]
    assert len(visible) == 1
    assert visible[0]["invoice_number"] == invoice["invoice_number"]
    assert visible[0]["balance_paise"] == 16520
    assert "clinic_id" not in visible[0]
    assert "patient_id" not in visible[0]
    assert "created_by_doctor_id" not in visible[0]


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

    receipts = client.get("/v1/dental/patient/receipts")
    assert receipts.status_code == 200
    assert len(receipts.json()["result"]) == 2
    assert all("idempotency_key" not in item for item in receipts.json()["result"])

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


def test_invoice_and_patient_receipts_are_isolated_by_workspace_and_patient(care_api):
    client, _store, actor, patient_actor, patient_a, _patient_b = care_api
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

    patient_actor["user_id"] = "supabase-user-b"
    assert client.get("/v1/dental/patient/invoices").json()["result"] == []
    assert client.get("/v1/dental/patient/receipts").json()["result"] == []


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


def test_doctor_and_patient_routes_remain_auth_protected():
    app = FastAPI()
    app.include_router(dental_patient_care.router)
    client = TestClient(app)
    assert client.get("/v1/dental/patient/invoices").status_code == 401
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
        json={"expected_version": 2, "status": "proposed", "patient_visible": True},
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


def test_patient_with_multiple_clinic_links_must_select_the_record(care_api):
    client, store, actor, patient_actor, patient_a, patient_b = care_api
    store._execute(
        "UPDATE dental_patients SET user_id=? WHERE id=?",
        ("supabase-user-a", patient_b["id"]),
    )
    actor["clinic_id"] = "clinic-a"
    plan_a = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/treatment-plans",
        json={"title": "Clinic A plan", "items": [{"description": "Assessment"}]},
    ).json()["result"]
    actor["clinic_id"] = "clinic-b"
    plan_b = client.post(
        f"/v1/dental/doctor/patients/{patient_b['id']}/treatment-plans",
        json={"title": "Clinic B plan", "items": [{"description": "Review"}]},
    ).json()["result"]
    actor["clinic_id"] = "clinic-a"
    share_a = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan_a['id']}",
        json={"status": "proposed", "patient_visible": True},
    )
    actor["clinic_id"] = "clinic-b"
    share_b = client.patch(
        f"/v1/dental/doctor/treatment-plans/{plan_b['id']}",
        json={"status": "proposed", "patient_visible": True},
    )
    assert share_a.status_code == 200
    assert share_b.status_code == 200

    linked = client.get("/v1/dental/patient/linked-records")
    assert linked.status_code == 200
    records = linked.json()["result"]
    assert {item["patient_id"] for item in records} == {patient_a["id"], patient_b["id"]}
    assert {item["clinic_name"] for item in records} == {"Main Clinic", "Other Clinic"}
    assert all("organisation_id" not in item for item in records)

    ambiguous = client.get("/v1/dental/patient/treatment-plans")
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"]["code"] == "PATIENT_RECORD_SELECTION_REQUIRED"

    clinic_a = client.get(
        "/v1/dental/patient/treatment-plans",
        params={"patient_id": patient_a["id"]},
    )
    clinic_b = client.get(
        "/v1/dental/patient/treatment-plans",
        params={"patient_id": patient_b["id"]},
    )
    assert clinic_a.status_code == 200
    assert [item["id"] for item in clinic_a.json()["result"]] == [plan_a["id"]]
    assert clinic_b.status_code == 200
    assert [item["id"] for item in clinic_b.json()["result"]] == [plan_b["id"]]

    patient_actor["user_id"] = "supabase-user-b"
    denied = client.get(
        "/v1/dental/patient/treatment-plans",
        params={"patient_id": patient_a["id"]},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_IDENTITY_MISMATCH"


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
