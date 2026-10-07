"""Dental application API with portable persistence and UIE adapter."""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, Field

from .dental_auth import login_google, require_doctor, request_otp, verify_otp
from .dental_store import get_dental_store
from .scano_adapter import get_scano_adapter
from .whatsapp_adapter import get_whatsapp_adapter
from .dental_billing import plans as membership_plans, create_order as create_membership_order, handle_webhook as handle_membership_webhook

router = APIRouter(prefix="/v1/dental", tags=["dental"])


class DoctorGoogleLoginIn(BaseModel):
    access_token: str = Field(min_length=20, max_length=10000)

class DoctorOtpRequestIn(BaseModel):
    phone: str = Field(min_length=7, max_length=40)

class DoctorOtpVerifyIn(BaseModel):
    phone: str = Field(min_length=7, max_length=40)
    challenge_id: str = Field(min_length=10, max_length=200)
    otp: str = Field(min_length=6, max_length=6)


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=40)
    age: str = Field(default="", max_length=10)


class AppointmentIn(BaseModel):
    patient_id: str
    starts_at: str
    treatment_type: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    status: str = "scheduled"


class AppointmentUpdateIn(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(requested|confirmed|scheduled|completed|cancelled|rescheduled|no_show)$")
    starts_at: str | None = Field(default=None, min_length=5, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class PublicAppointmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=7, max_length=40)
    starts_at: str = Field(min_length=5, max_length=80)
    treatment_type: str = Field(default="General dental consultation", min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    intelligence_request: bool = False


class ChartEntryIn(BaseModel):
    patient_id: str
    tooth_fdi: str = Field(pattern=r"^(1[1-8]|2[1-8]|3[1-8]|4[1-8])$")
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=4000)


class PeriodontalEntryIn(BaseModel):
    patient_id: str
    tooth_fdi: str = Field(pattern=r"^(1[1-8]|2[1-8]|3[1-8]|4[1-8])$")
    measurements: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default="", max_length=4000)


class IntelligenceIn(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    patient_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None
    locale: str | None = None


class ScanScreenIn(BaseModel):
    patient_id: str | None = None
    scan_reference: str = Field(min_length=1, max_length=4000)
    scan_type: str = Field(default="oral_screening", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WhatsAppTemplateIn(BaseModel):
    template_name: str = Field(min_length=1, max_length=200)
    to: str = Field(min_length=7, max_length=40)
    language_code: str = Field(default="en", min_length=2, max_length=20)
    components: list[dict[str, Any]] = Field(default_factory=list)


class MembershipOrderIn(BaseModel):
    plan_id: str = Field(min_length=3, max_length=40)

@router.get("/billing/plans")
async def get_membership_plans():
    return {"plans": membership_plans()}

@router.get("/billing/membership")
async def get_membership(doctor=Depends(require_doctor)):
    clinic_id = doctor.get("clinic_id", "dr-pranali")
    master = clinic_id == os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()
    if master:
        return {"clinic_id": clinic_id, "plan_id": "master", "name": "Dr. Pranali Master", "status": "active", "price_inr": 0, "period": "forever", "master": True}
    store = get_dental_store()
    rows = store._execute("SELECT * FROM dental_memberships WHERE clinic_id=%s ORDER BY created_at DESC LIMIT 1" if store.database_url else "SELECT * FROM dental_memberships WHERE clinic_id=? ORDER BY created_at DESC LIMIT 1", (clinic_id,))
    return {"clinic_id": clinic_id, "membership": rows[0] if rows else None, "master": False}

@router.post("/billing/order")
async def create_membership_order(body: MembershipOrderIn, doctor=Depends(require_doctor)):
    clinic_id = doctor.get("clinic_id")
    if not clinic_id:
        raise HTTPException(400, "Clinic context is required")
    return await create_membership_order(clinic_id, body.plan_id, doctor.get("id", ""))

@router.post("/billing/razorpay/webhook")
async def membership_webhook(request: Request, x_razorpay_signature: str = Header(default="")):
    return handle_membership_webhook(await request.body(), x_razorpay_signature)

@router.post("/auth/google")
async def doctor_google_login(body: DoctorGoogleLoginIn):
    return await login_google(body.access_token)

@router.post("/auth/otp/request")
async def doctor_otp_request(body: DoctorOtpRequestIn):
    return await request_otp(body.phone)

@router.post("/auth/otp/verify")
async def doctor_otp_verify(body: DoctorOtpVerifyIn):
    return await verify_otp(body.phone, body.challenge_id, body.otp)

@router.post("/auth/logout")
async def doctor_logout(doctor=Depends(require_doctor)):
    # Sessions are short-lived and can be invalidated later through the session store.
    return {"ok": True}

@router.get("/health")
async def dental_health():
    store = get_dental_store()
    return {"status": "ok", "service": "dental-api", "database": store.backend,
            "uie_configured": bool(os.getenv("UIE_API_URL") and os.getenv("UIE_API_KEY"))}


@router.get("/patients")
async def list_patients(actor: dict = Depends(require_doctor)):
    return get_dental_store().patients(actor.get("clinic_id"))


@router.post("/patients")
async def create_patient(body: PatientIn, actor: dict = Depends(require_doctor)):
    store = get_dental_store()
    item = store.create_patient(body.model_dump(), actor.get("clinic_id"))
    store.audit(actor.get("user_id"), "create", "patient", item["id"])
    return item


@router.post("/public/appointments")
async def create_public_appointment(body: PublicAppointmentIn):
    """Create a patient appointment request without exposing doctor credentials.

    Public intake deliberately creates a limited requested appointment. It does
    not run AI, expose patient/doctor records, or accept an actor/admin identity.
    """
    store = get_dental_store()
    master_clinic = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"
    patient = store.create_patient({"name": body.name.strip(), "phone": body.phone.strip(), "age": ""}, master_clinic)
    appointment = store.create_appointment({
        "clinic_id": master_clinic,
        "patient_id": patient["id"],
        "starts_at": body.starts_at.strip(),
        "treatment_type": body.treatment_type.strip(),
        "note": (body.note or "").strip(),
        "status": "requested",
    })
    store.audit("public-intake", "request", "appointment", appointment["id"])
    return {"status": "requested", "appointment": appointment}


@router.post("/scans/screen")
async def screen_scan(body: ScanScreenIn, actor: dict = Depends(require_doctor)):
    adapter = get_scano_adapter()
    try:
        result = await adapter.screen({
            "patient_id": body.patient_id,
            "scan_reference": body.scan_reference,
            "scan_type": body.scan_type,
            "metadata": body.metadata,
        })
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    get_dental_store().audit(actor.get("user_id"), "screen", "scan", body.patient_id)
    return result


@router.post("/whatsapp/template")
async def send_whatsapp_template(body: WhatsAppTemplateIn, actor: dict = Depends(require_doctor)):
    adapter = get_whatsapp_adapter()
    try:
        result = await adapter.send_template(body.to.strip(), body.template_name.strip(), body.language_code.strip(), body.components)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    get_dental_store().audit(actor.get("user_id"), "send", "whatsapp", body.to.strip())
    return result


@router.get("/appointments")
async def list_appointments(actor: dict = Depends(require_doctor)):
    store = get_dental_store()
    clinic_id = actor.get("clinic_id")
    patients = {p["id"]: p for p in store.patients(clinic_id)}
    return [
        {**appointment,
         "patient_name": patients.get(appointment["patient_id"], {}).get("name", ""),
         "patient_phone": patients.get(appointment["patient_id"], {}).get("phone", "")}
        for appointment in store.appointments(clinic_id)
    ]


@router.patch("/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, body: AppointmentUpdateIn, actor: dict = Depends(require_doctor)):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="At least one appointment field is required")
    store = get_dental_store()
    item = store.update_appointment(appointment_id, changes, actor.get("clinic_id"))
    if not item:
        raise HTTPException(status_code=404, detail="Appointment not found")
    store.audit(actor.get("user_id"), "update", "appointment", appointment_id)
    return item


@router.post("/appointments")
async def create_appointment(body: AppointmentIn, actor: dict = Depends(require_doctor)):
    store = get_dental_store()
    item = store.create_appointment(body.model_dump(), actor.get("clinic_id"))
    store.audit(actor.get("user_id"), "create", "appointment", item["id"])
    return item


@router.get("/patients/{patient_id}/chart")
async def get_chart(patient_id: str, actor: dict = Depends(require_doctor)):
    return get_dental_store().chart(patient_id, actor.get("clinic_id"))


@router.post("/patients/{patient_id}/chart")
async def save_chart(patient_id: str, body: ChartEntryIn, actor: dict = Depends(require_doctor)):
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    store = get_dental_store()
    item = store.save_chart(body.model_dump(), actor.get("clinic_id"))
    store.audit(actor.get("user_id"), "write", "dental_chart", item["id"])
    return item


@router.get("/patients/{patient_id}/periodontogram")
async def get_periodontogram(patient_id: str, actor: dict = Depends(require_doctor)):
    return get_dental_store().periodontogram(patient_id, actor.get("clinic_id"))


@router.post("/patients/{patient_id}/periodontogram")
async def save_periodontogram(patient_id: str, body: PeriodontalEntryIn, actor: dict = Depends(require_doctor)):
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    item = get_dental_store().save_periodontogram(body.model_dump(), actor.get("clinic_id"))
    get_dental_store().audit(actor.get("user_id"), "write", "periodontogram", item["id"])
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
    store.record_ai(body.patient_id, body.goal, result, actor.get("clinic_id"))
    store.audit(actor.get("user_id"), "run", "dental_intelligence", body.patient_id)
    return result
