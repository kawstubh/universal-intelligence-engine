"""Dental application API with portable persistence and UIE adapter."""
from __future__ import annotations

import os
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .dental_auth import require_doctor
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental"])


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=40)
    age: str = Field(default="", max_length=10)


class AppointmentIn(BaseModel):
    patient_id: str
    starts_at: str
    treatment_type: str = Field(min_length=1, max_length=120)
    status: str = "scheduled"




class PublicAppointmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=7, max_length=40)
    starts_at: str = Field(min_length=5, max_length=80)
    treatment_type: str = Field(default="General dental consultation", min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    intelligence_request: dict[str, Any] | None = None


class ScanOScreeningIn(BaseModel):
    """Provider-neutral envelope for scanO screening results."""
    external_id: str = Field(min_length=1, max_length=200)
    patient_id: str | None = None
    screening_id: str | None = Field(default=None, max_length=200)
    occurred_at: str | None = Field(default=None, max_length=80)
    risk_score: float | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    raw_result: dict[str, Any] = Field(default_factory=dict)


class ChartEntryIn(BaseModel):
    patient_id: str
    tooth_fdi: str = Field(pattern=r"^(1[1-8]|2[1-8]|3[1-8]|4[1-8])$")
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=4000)


class IntelligenceIn(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    patient_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None
    locale: str | None = None


@router.get("/health")
async def dental_health():
    store = get_dental_store()
    return {"status": "ok", "service": "dental-api", "database": store.backend,
            "uie_configured": bool(os.getenv("UIE_API_URL") and os.getenv("UIE_API_KEY"))}


@router.post("/integrations/scano/screening")
async def ingest_scano_screening(
    body: ScanOScreeningIn,
    authorization: str | None = Header(default=None),
):
    """Ingest a scanO screening result into the dental intelligence layer."""
    expected = os.getenv("SCANO_INTEGRATION_KEY")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="scanO integration authorization required")

    store = get_dental_store()
    item = store.record_screening({
        "source": "scanO",
        "external_id": body.external_id,
        "patient_id": body.patient_id,
        "screening_id": body.screening_id,
        "occurred_at": body.occurred_at,
        "risk_score": body.risk_score,
        "findings": body.findings,
        "evidence": body.evidence,
        "raw_result": body.raw_result,
    })
    store.audit("scanO-integration", "ingest", "screening", item["id"])
    return {"status": "accepted", "screening": item}


@router.get("/patients")
async def list_patients(_: dict = Depends(require_doctor)):
    return get_dental_store().patients()


@router.post("/patients")
async def create_patient(body: PatientIn, actor: dict = Depends(require_doctor)):
    store = get_dental_store()
    item = store.create_patient(body.model_dump())
    store.audit(actor.get("user_id"), "create", "patient", item["id"])
    return item


@router.post("/public/appointments")
async def create_public_appointment(body: PublicAppointmentIn):
    """Create a patient appointment request without exposing doctor credentials.

    Public intake deliberately creates a limited requested appointment. It does
    not run AI, expose patient/doctor records, or accept an actor/admin identity.
    """
    store = get_dental_store()
    patient = store.create_patient({"name": body.name.strip(), "phone": body.phone.strip(), "age": ""})
    appointment = store.create_appointment({
        "patient_id": patient["id"],
        "starts_at": body.starts_at.strip(),
        "treatment_type": body.treatment_type.strip(),
        "status": "requested",
    })
    store.audit("public-intake", "request", "appointment", appointment["id"])
    return {"status": "requested", "appointment": appointment}


@router.get("/appointments")
async def list_appointments(_: dict = Depends(require_doctor)):
    return get_dental_store().appointments()


@router.post("/appointments")
async def create_appointment(body: AppointmentIn, actor: dict = Depends(require_doctor)):
    store = get_dental_store()
    item = store.create_appointment(body.model_dump())
    store.audit(actor.get("user_id"), "create", "appointment", item["id"])
    return item


@router.get("/patients/{patient_id}/chart")
async def get_chart(patient_id: str, _: dict = Depends(require_doctor)):
    return get_dental_store().chart(patient_id)


@router.post("/patients/{patient_id}/chart")
async def save_chart(patient_id: str, body: ChartEntryIn, actor: dict = Depends(require_doctor)):
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    store = get_dental_store()
    item = store.save_chart(body.model_dump())
    store.audit(actor.get("user_id"), "write", "dental_chart", item["id"])
    return item


@router.post("/intelligence/run")
async def run_dental_intelligence(body: IntelligenceIn, actor: dict = Depends(require_doctor)):
    allowed = {"patient_context", "appointments", "dental_chart", "screening",
               "care_pathway", "referral", "practice"}
    safe_context = {k: v for k, v in body.context.items() if k in allowed}
    safe_context.update({"domain": "dental", "patient_id": body.patient_id})

    uie_url, uie_key = os.getenv("UIE_API_URL"), os.getenv("UIE_API_KEY")
    if not uie_url or not uie_key:
        raise HTTPException(status_code=503, detail="UIE backend is not configured")

    payload = {
        "goal": body.goal, "language": body.language, "locale": body.locale,
        "context": safe_context,
        "constraints": {"require_sources": True, "human_approval_required": True,
                        "domain": "dental"},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{uie_url.rstrip('/')}/v1/intelligence/run",
            headers={"Authorization": f"Bearer {uie_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"UIE request failed: {response.text[:500]}")

    result = response.json()
    store = get_dental_store()
    store.record_ai(body.patient_id, body.goal, result)
    store.audit(actor.get("user_id"), "run", "dental_intelligence", body.patient_id)
    return result
