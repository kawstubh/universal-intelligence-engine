"""Doctor-only tenant-scoped treatment plans and clinic billing.

Patient account, linked-record, patient billing and patient appointment routes
are not mounted. Public guest booking is handled by the separate public API.
No payment processor is invoked here; receipts record payments already
received by the clinic and never store card credentials.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

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
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=2, max_length=160)
    summary: str | None = Field(default=None, max_length=2000)
    status: Literal["proposed", "accepted", "in_progress", "completed", "cancelled"] | None = None
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


@router.get("/doctor/dashboard/summary")
def doctor_dashboard_summary(actor: Annotated[dict, Depends(require_doctor)]):
    """Return non-identifying live metrics for the doctor's active clinic workspace."""
    summary = get_dental_store().dashboard_summary(actor["clinic_id"])
    return _envelope(summary)
