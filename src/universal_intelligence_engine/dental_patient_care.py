"""Tenant-scoped dental treatment plans, invoices and receipts.

This module stores clinic-owned records only. Patient routes are read-only and
return only records explicitly shared by the treating clinic. No payment
processor is invoked here; receipts record payments already received by the
clinic and never store card credentials.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dental_ai import require_patient
from .dental_auth import require_doctor
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-patient-care"])


class TreatmentPlanItemIn(BaseModel):
    service_code: str = Field(default="", max_length=80)
    description: str = Field(min_length=1, max_length=500)
    tooth_fdi: str | None = Field(default=None, pattern=r"^[1-8][1-8]$")


class TreatmentPlanCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    summary: str = Field(default="", max_length=2000)
    items: list[TreatmentPlanItemIn] = Field(min_length=1, max_length=50)


class TreatmentPlanUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    summary: str | None = Field(default=None, max_length=2000)
    status: Literal["proposed", "accepted", "in_progress", "completed", "cancelled"] | None = None
    patient_visible: bool | None = None
    items: list[TreatmentPlanItemIn] | None = Field(default=None, min_length=1, max_length=50)
    expected_version: int | None = Field(default=None, ge=1)


class InvoiceItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity_milli: int = Field(gt=0, le=1_000_000)
    unit_price_paise: int = Field(ge=0, le=100_000_000)
    tax_rate_bps: int = Field(default=0, ge=0, le=2800)


class InvoiceCreateIn(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=50)
    discount_paise: int = Field(default=0, ge=0, le=100_000_000)
    patient_note: str = Field(default="", max_length=1000)


class InvoiceIssueIn(BaseModel):
    due_date: date | None = None


class ReceiptCreateIn(BaseModel):
    amount_paise: int = Field(gt=0, le=100_000_000)
    method: Literal["cash", "upi", "bank_transfer", "razorpay", "other"]
    reference: str = Field(default="", max_length=128)
    idempotency_key: str = Field(min_length=16, max_length=128)
    paid_at: datetime | None = None


def _envelope(result):
    return {
        "result": result,
        "confidence": 1.0,
        "sources": [],
        "request_id": str(uuid.uuid4()),
    }


def _patient_for_actor(actor: dict, patient_id: str | None = None):
    store = get_dental_store()
    if patient_id:
        patient = store.patient_by_user_id(actor["user_id"], patient_id)
        if not patient:
            raise HTTPException(
                403,
                detail={
                    "code": "PATIENT_IDENTITY_MISMATCH",
                    "message": "The selected clinic record is not linked to this patient account.",
                },
            )
        return patient
    linked = store.patients_by_user_id(actor["user_id"])
    if len(linked) > 1:
        raise HTTPException(
            409,
            detail={
                "code": "PATIENT_RECORD_SELECTION_REQUIRED",
                "message": "Choose the clinic record you want to view.",
            },
        )
    if linked:
        return store.patient_by_user_id(actor["user_id"], linked[0]["patient_id"])
    raise HTTPException(
        404,
        detail={
            "code": "PATIENT_RECORD_NOT_LINKED",
            "message": "No clinic record is linked to this patient account.",
        },
    )


def _public_plan(plan: dict) -> dict:
    return {
        "id": plan["id"],
        "title": plan["title"],
        "summary": plan["summary"],
        "status": plan["status"],
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
        "items": [
            {
                "id": item["id"],
                "service_code": item["service_code"],
                "description": item["description"],
                "tooth_fdi": item["tooth_fdi"],
                "status": item["status"],
                "sort_order": item["sort_order"],
            }
            for item in plan.get("items", [])
        ],
    }


def _public_receipt(receipt: dict) -> dict:
    return {
        "id": receipt["id"],
        "invoice_id": receipt["invoice_id"],
        "receipt_number": receipt["receipt_number"],
        "amount_paise": int(receipt["amount_paise"]),
        "method": receipt["method"],
        "reference": receipt["reference"],
        "paid_at": receipt["paid_at"],
        "created_at": receipt["created_at"],
    }


def _public_invoice(invoice: dict) -> dict:
    return {
        "id": invoice["id"],
        "invoice_number": invoice["invoice_number"],
        "currency": invoice["currency"],
        "status": invoice["status"],
        "subtotal_paise": int(invoice["subtotal_paise"]),
        "discount_paise": int(invoice["discount_paise"]),
        "tax_paise": int(invoice["tax_paise"]),
        "total_paise": int(invoice["total_paise"]),
        "paid_paise": int(invoice["paid_paise"]),
        "balance_paise": int(invoice["balance_paise"]),
        "patient_note": invoice["patient_note"],
        "due_date": invoice["due_date"],
        "issued_at": invoice["issued_at"],
        "created_at": invoice["created_at"],
        "items": [
            {
                "id": item["id"],
                "description": item["description"],
                "quantity_milli": int(item["quantity_milli"]),
                "unit_price_paise": int(item["unit_price_paise"]),
                "tax_rate_bps": int(item["tax_rate_bps"]),
                "subtotal_paise": int(item["subtotal_paise"]),
                "discount_paise": int(item.get("discount_paise", 0)),
                "tax_paise": int(item["tax_paise"]),
                "total_paise": int(item["total_paise"]),
                "sort_order": item["sort_order"],
            }
            for item in invoice.get("items", [])
        ],
        "receipts": [_public_receipt(item) for item in invoice.get("receipts", [])],
    }


def _bad_request(exc: ValueError):
    message = str(exc)
    status = 404 if "not found" in message.lower() else 409 if any(
        word in message.lower() for word in ("already", "only ", "exceeds", "cannot", "must be proposed", "version conflict", "cannot transition")
    ) else 400
    raise HTTPException(
        status,
        detail={"code": "DENTAL_RECORD_CONFLICT" if status == 409 else "DENTAL_RECORD_INVALID", "message": message},
    ) from exc


@router.post("/doctor/patients/{patient_id}/treatment-plans")
def create_treatment_plan(
    patient_id: str,
    body: TreatmentPlanCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    try:
        result = store.create_treatment_plan(
            {"patient_id": patient_id, **body.model_dump()}, actor["clinic_id"], actor["id"]
        )
    except ValueError as exc:
        _bad_request(exc)
    return _envelope(result)


@router.get("/doctor/patients/{patient_id}/treatment-plans")
def list_doctor_treatment_plans(
    patient_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    return _envelope(store.treatment_plans(actor["clinic_id"], patient_id))


@router.patch("/doctor/treatment-plans/{plan_id}")
def update_treatment_plan(
    plan_id: str,
    body: TreatmentPlanUpdateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    changes = body.model_dump(exclude_unset=True)
    try:
        if "items" in changes and changes["items"] is not None:
            changes["items"] = [item.model_dump() if hasattr(item, "model_dump") else item for item in changes["items"]]
        result = get_dental_store().update_treatment_plan(
            plan_id, actor["clinic_id"], actor["id"], changes
        )
    except ValueError as exc:
        _bad_request(exc)
    if result is None:
        raise HTTPException(404, detail={"code": "TREATMENT_PLAN_NOT_FOUND", "message": "Treatment plan not found in this workspace"})
    return _envelope(result)


@router.get("/patient/linked-records")
def list_patient_linked_records(actor: Annotated[dict, Depends(require_patient)]):
    records = get_dental_store().patients_by_user_id(actor["user_id"])
    return _envelope([
        {
            "patient_id": item["patient_id"],
            "patient_name": item["patient_name"],
            "clinic_name": item["clinic_name"],
            "organisation_name": item.get("organisation_name") or item["clinic_name"],
        }
        for item in records
    ])


@router.get("/patient/treatment-plans")
def list_patient_treatment_plans(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    plans = get_dental_store().treatment_plans(
        patient["clinic_id"], patient["id"], patient_visible_only=True
    )
    return _envelope([_public_plan(item) for item in plans])


@router.post("/doctor/patients/{patient_id}/invoices")
def create_invoice(
    patient_id: str,
    body: InvoiceCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    try:
        result = store.create_invoice(
            {"patient_id": patient_id, **body.model_dump()}, actor["clinic_id"], actor["id"]
        )
    except ValueError as exc:
        _bad_request(exc)
    return _envelope(result)


@router.get("/doctor/patients/{patient_id}/invoices")
def list_doctor_invoices(
    patient_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    return _envelope(store.invoices(actor["clinic_id"], patient_id))


@router.post("/doctor/invoices/{invoice_id}/issue")
def issue_invoice(
    invoice_id: str,
    body: InvoiceIssueIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    try:
        result = get_dental_store().issue_invoice(
            invoice_id, actor["clinic_id"], actor["id"],
            body.due_date.isoformat() if body.due_date else None,
        )
    except ValueError as exc:
        _bad_request(exc)
    if result is None:
        raise HTTPException(404, detail={"code": "INVOICE_NOT_FOUND", "message": "Invoice not found in this workspace"})
    return _envelope(result)


@router.post("/doctor/invoices/{invoice_id}/receipts")
def record_invoice_receipt(
    invoice_id: str,
    body: ReceiptCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    data = body.model_dump()
    data["paid_at"] = body.paid_at.isoformat() if body.paid_at else None
    try:
        result = get_dental_store().record_invoice_receipt(
            invoice_id, actor["clinic_id"], actor["id"], data
        )
    except ValueError as exc:
        _bad_request(exc)
    return _envelope(result)


@router.post("/doctor/invoices/{invoice_id}/void")
def void_invoice(
    invoice_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    try:
        result = get_dental_store().void_invoice(
            invoice_id, actor["clinic_id"], actor["id"]
        )
    except ValueError as exc:
        _bad_request(exc)
    if result is None:
        raise HTTPException(404, detail={"code": "INVOICE_NOT_FOUND", "message": "Invoice not found in this workspace"})
    return _envelope(result)


@router.get("/patient/invoices")
def list_patient_invoices(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    invoices = get_dental_store().invoices(
        patient["clinic_id"], patient["id"], patient_visible_only=True
    )
    return _envelope([_public_invoice(item) for item in invoices])


@router.get("/patient/receipts")
def list_patient_receipts(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    receipts = get_dental_store().receipts(
        patient["clinic_id"], patient["id"], patient_visible_only=True
    )
    return _envelope([_public_receipt(item) for item in receipts])


@router.get("/doctor/dashboard/summary")
def doctor_dashboard_summary(actor: Annotated[dict, Depends(require_doctor)]):
    """Return non-identifying live metrics for the doctor's active clinic workspace."""
    summary = get_dental_store().dashboard_summary(actor["clinic_id"])
    return _envelope(summary)
