"""AI safety, limits, patient assistant, and clinical AI endpoints."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .dental_auth import require_doctor
from .dental_store import get_dental_store
from .scano_adapter import get_scano_adapter

router = APIRouter(prefix="/v1/dental/ai", tags=["dental-ai"])
DISCLAIMER_DOCTOR = "AI suggestion only. A qualified doctor must verify the result. AI never writes clinical records automatically."
DISCLAIMER_PATIENT = "This AI assistant is not a diagnosis. It cannot prescribe medicines or provide prescription/dosage advice."


class DoctorAIIn(BaseModel):
    patient_id: str = Field(min_length=1, max_length=128)
    language: str = Field(default="en", max_length=20)


class ScanAIIn(DoctorAIIn):
    scan_reference: str = Field(min_length=1, max_length=4000)
    scan_type: str = Field(default="oral_screening", max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NoteAIIn(DoctorAIIn):
    visit_context: str = Field(default="", max_length=4000)


class PatientLinkIn(BaseModel):
    invite_code: str = Field(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9]+$")


class PatientAIIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    patient_id: str | None = None
    language: str = Field(default="en", max_length=20)


class PatientPlanIn(BaseModel):
    patient_id: str | None = None
    treatment_plan: str = Field(min_length=1, max_length=12000)
    language: str = Field(default="en", max_length=20)


class PatientAftercareIn(BaseModel):
    patient_id: str | None = None
    treatment: str = Field(min_length=1, max_length=2000)
    language: str = Field(default="en", max_length=20)


class PatientTriageIn(BaseModel):
    patient_id: str | None = None
    symptoms: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="en", max_length=20)


class ConsentIn(BaseModel):
    consent: bool


class DeviceIn(BaseModel):
    patient_id: str | None = None
    expo_push_token: str = Field(min_length=10, max_length=512)


class ApprovalIn(BaseModel):
    patient_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=2, max_length=100)
    action: str = Field(pattern=r"^(accepted|edited|dismissed)$")
    content: dict[str, Any] = Field(default_factory=dict)


def _result(result: Any, confidence: Any = None, sources: list[Any] | None = None, disclaimer: str = DISCLAIMER_DOCTOR, **extra) -> dict[str, Any]:
    return {
        "result": result,
        "confidence": confidence,
        "sources": sources or [],
        "disclaimers": [disclaimer],
        "request_id": str(uuid.uuid4()),
        **extra,
    }


def _cache_key(capability: str, payload: dict[str, Any]) -> str:
    raw = json.dumps({"capability": capability, "payload": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _limits(actor_id: str, clinic_id: str, endpoint: str) -> None:
    store = get_dental_store()
    now = int(time.time())
    user_window = now - 86400
    month_window = now - 31 * 86400
    user_limit = int(os.getenv("DENTAL_AI_USER_DAILY_CAP", "50"))
    clinic_limit = int(os.getenv("DENTAL_AI_CLINIC_MONTHLY_CAP", "1000"))
    if store.ai_usage_count(actor_id, user_window) >= user_limit:
        raise HTTPException(429, detail={"message": "AI daily user limit reached", "code": "AI_USER_RATE_LIMIT"})
    if store.ai_usage_count(clinic_id, month_window, clinic_id=clinic_id) >= clinic_limit:
        raise HTTPException(429, detail={"message": "Clinic monthly AI limit reached", "code": "AI_CLINIC_MONTHLY_CAP"})


def _patient_data_mode() -> str:
    mode = os.getenv("AI_PATIENT_DATA_MODE", "anonymized").strip().lower()
    return mode if mode in {"anonymized", "full"} else "anonymized"


def _age_band(age: Any) -> str:
    try:
        value = int(age)
    except (TypeError, ValueError):
        return "unknown"
    if value < 18:
        return "under_18"
    if value < 30:
        return "18_29"
    if value < 45:
        return "30_44"
    if value < 60:
        return "45_59"
    return "60_plus"


def _safe_context(patient_id: str, clinic_id: str) -> dict[str, Any]:
    store = get_dental_store()
    patient = store.get_patient(patient_id, clinic_id)
    if not patient:
        raise HTTPException(403, detail={"message": "Patient is not accessible from this clinic", "code": "PATIENT_CLINIC_ACCESS_DENIED"})
    chart = [
        {"tooth_fdi": row["tooth_fdi"], "status": row["status"]}
        for row in store.chart(patient_id, clinic_id)
    ]
    perio = [
        {"tooth_fdi": row["tooth_fdi"], "measurements": row.get("measurements", {})}
        for row in store.periodontogram(patient_id, clinic_id)
    ]
    appointments = [
        {"starts_at": row["starts_at"], "treatment_type": row["treatment_type"], "status": row["status"]}
        for row in store.appointments(clinic_id, patient_id)
    ]
    context = {
        "domain": "dental",
        "patient": {"patient_id": patient["id"], "age": patient.get("age", "")},
        "dental_chart": chart,
        "periodontogram": perio,
        "appointments": appointments,
        "patient_id": patient_id,
    }
    if _patient_data_mode() == "anonymized":
        context["patient"] = {"age_band": _age_band(patient.get("age"))}
        context["appointments"] = [
            {"treatment_type": row["treatment_type"], "status": row["status"]}
            for row in appointments
        ]
        context.pop("patient_id", None)
    return context


async def _uie(capability: str, goal: str, context: dict[str, Any], disclaimer: str = DISCLAIMER_DOCTOR) -> dict[str, Any]:
    url = os.getenv("UIE_API_URL", "").strip()
    key = os.getenv("UIE_API_KEY", "").strip()
    if not url or not key:
        raise HTTPException(503, detail={"message": "UIE bridge is not configured", "code": "UIE_BACKEND_NOT_CONFIGURED"})
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{url.rstrip('/')}/v1/intelligence/run",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "goal": goal,
                    "context": context,
                    "language": context.get("language"),
                    "locale": context.get("locale"),
                    "constraints": {
                        "require_sources": True,
                        "human_approval_required": True,
                        "do_not_write_records": True,
                        "domain": "dental",
                        "capability": capability,
                    },
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail={"message": "Clinical AI took too long to respond. Please retry.", "code": "UIE_TIMEOUT"}) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail={"message": "Clinical AI service could not be reached. Please retry.", "code": "UIE_UNREACHABLE"}) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict) and detail.get("code") == "PROVIDER_NOT_CONFIGURED":
            raise HTTPException(503, detail=detail)
        raise HTTPException(502, detail={"message": "Clinical AI service returned an error. Please retry.", "code": "UIE_REQUEST_FAILED"})
    body = response.json()
    return _result(
        body.get("answer"),
        body.get("confidence"),
        body.get("evidence") or [],
        disclaimer,
        actions=body.get("actions") or [],
        capability=capability,
    )


async def _doctor_call(capability: str, body: DoctorAIIn, actor: dict, goal: str) -> dict[str, Any]:
    clinic_id = str(actor.get("clinic_id") or "")
    _limits(actor["id"], clinic_id, capability)
    context = _safe_context(body.patient_id, clinic_id)
    context["language"] = body.language
    key = _cache_key(capability, context)
    cached = get_dental_store().get_ai_cache(key)
    if cached:
        get_dental_store().record_ai_usage(actor["id"], clinic_id, "doctor", capability, True)
        cached["request_id"] = str(uuid.uuid4())
        cached["cache_hit"] = True
        return cached
    result = await _uie(capability, goal, context)
    result["cache_hit"] = False
    get_dental_store().put_ai_cache(key, result, int(time.time()) + int(os.getenv("DENTAL_AI_CACHE_TTL", "900")))
    get_dental_store().record_ai_usage(actor["id"], clinic_id, "doctor", capability, False)
    return result


@router.post("/doctor/patient-summary")
async def doctor_patient_summary(body: DoctorAIIn, actor=Depends(require_doctor)):
    return await _doctor_call("patient_summary", body, actor, "Provide a concise patient summary and evidence-based risk flags from the authorized dental record. Separate known findings, risk signals, and uncertainty. Do not invent diagnoses.")


@router.post("/doctor/treatment-plan")
async def doctor_treatment_plan(body: DoctorAIIn, actor=Depends(require_doctor)):
    return await _doctor_call("treatment_plan", body, actor, "Suggest evidence-based treatment-plan options from the authorized dental record, including rationale, alternatives, uncertainty, and verification points. Never write a treatment plan.")


@router.post("/doctor/chart-insights")
async def doctor_chart_insights(body: DoctorAIIn, actor=Depends(require_doctor)):
    return await _doctor_call("chart_insights", body, actor, "Analyze dental chart and periodontogram patterns, highlighting teeth or findings that merit clinician review. Do not diagnose or alter records.")


@router.post("/doctor/clinical-note")
async def doctor_clinical_note(body: NoteAIIn, actor=Depends(require_doctor)):
    result = await _doctor_call("clinical_note", body, actor, "Draft a concise clinical note from the authorized dental record and the supplied visit context. Mark missing information and do not invent findings.")
    return result


@router.post("/doctor/follow-up")
async def doctor_follow_up(body: DoctorAIIn, actor=Depends(require_doctor)):
    return await _doctor_call("follow_up", body, actor, "Recommend follow-up timing and recall considerations based only on the authorized dental record. Give reasons and uncertainties; do not create an appointment.")


@router.post("/doctor/daily-summary")
async def doctor_daily_summary(actor=Depends(require_doctor)):
    clinic_id = actor["clinic_id"]
    _limits(actor["id"], clinic_id, "daily_summary")
    store = get_dental_store()
    appointments = store.appointments(clinic_id)
    safe = [{"starts_at": a["starts_at"], "treatment_type": a["treatment_type"], "status": a["status"]} for a in appointments]
    return await _uie("daily_summary", "Summarize today's clinic workload, appointment statuses, and operational follow-up items. Do not include patient names or identifiers.", {"domain": "dental", "appointments": safe})


@router.post("/doctor/scan-analysis")
async def doctor_scan_analysis(body: ScanAIIn, actor=Depends(require_doctor)):
    clinic_id = actor["clinic_id"]
    _limits(actor["id"], clinic_id, "scan_analysis")
    _safe_context(body.patient_id, clinic_id)
    adapter = get_scano_adapter()
    try:
        scan = await adapter.screen({
            "patient_id": body.patient_id,
            "scan_reference": body.scan_reference,
            "scan_type": body.scan_type,
            "metadata": body.metadata,
        })
    except RuntimeError as exc:
        return _result(
            {"status": "adapter_unavailable", "message": str(exc)},
            confidence=None,
            sources=[],
            disclaimer=DISCLAIMER_DOCTOR,
            adapter={"provider": "scanO", "mode": adapter.mode, "configured": adapter.configured},
        )
    result = await _uie("scan_analysis", "Review this scanO adapter result for clinically relevant dental observations. Do not diagnose and do not write records.", {"domain": "dental", "scan": scan, "patient_id": body.patient_id})
    result["adapter"] = {"provider": "scanO", "mode": adapter.mode, "configured": adapter.configured, "status": scan.get("status")}
    get_dental_store().record_ai_usage(actor["id"], clinic_id, "doctor", "scan_analysis", False)
    return result


@router.post("/doctor/approve")
async def doctor_approve(body: ApprovalIn, actor=Depends(require_doctor)):
    clinic_id = actor["clinic_id"]
    _safe_context(body.patient_id, clinic_id)
    approval_id = get_dental_store().save_ai_approval(
        clinic_id, body.patient_id, actor["id"], body.capability, body.action, body.content
    )
    return {"result": {"approval_id": approval_id, "action": body.action, "written_to_clinical_record": False}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_DOCTOR], "request_id": str(uuid.uuid4())}


async def _supabase_user(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer access token required")
    token = authorization[7:].strip()
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not token or not url or not key:
        raise HTTPException(401, detail={"message": "Invalid patient session", "code": "INVALID_PATIENT_SESSION"})
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{url}/auth/v1/user", headers={"apikey": key, "Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(401, detail={"message": "Invalid patient session", "code": "INVALID_PATIENT_SESSION"})
    user = response.json()
    if not user.get("id"):
        raise HTTPException(401, detail={"message": "Patient identity missing", "code": "PATIENT_IDENTITY_MISSING"})
    return user


async def require_patient(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = await _supabase_user(authorization)
    return {"user_id": str(user["id"]), "email": str(user.get("email") or "")}


async def _patient_record(actor: dict, patient_id: str | None = None) -> dict[str, Any] | None:
    store = get_dental_store()
    if patient_id:
        row = store.patient_by_user_id(actor["user_id"], patient_id)
        if not row:
            raise HTTPException(403, detail={"message": "Patient record is not linked to this account", "code": "PATIENT_IDENTITY_MISMATCH"})
        return row
    return store.patient_by_user_id(actor["user_id"])


@router.post("/patient/link")
async def patient_link(body: PatientLinkIn, actor=Depends(require_patient)):
    patient = get_dental_store().consume_patient_invite(body.invite_code, actor["user_id"])
    if not patient:
        raise HTTPException(403, detail={"message": "Invalid, expired, or already-used clinic invite code", "code": "PATIENT_INVITE_INVALID"})
    return {"result": {"patient_id": patient["id"], "clinic_id": patient["clinic_id"], "linked": True}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.post("/doctor/patients/{patient_id}/invite")
async def create_patient_invite(patient_id: str, actor=Depends(require_doctor)):
    clinic_id = actor["clinic_id"]
    if not get_dental_store().get_patient(patient_id, clinic_id):
        raise HTTPException(404, "Patient not found")
    code = get_dental_store().create_patient_invite(patient_id, clinic_id)
    return {"result": {"patient_id": patient_id, "invite_code": code, "expires_in": 86400}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_DOCTOR], "request_id": str(uuid.uuid4())}


@router.post("/patient/consent")
async def patient_consent(body: ConsentIn, actor=Depends(require_patient)):
    get_dental_store().set_ai_consent(actor["user_id"], bool(body.consent))
    return {"result": {"consent": bool(body.consent)}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


def _triage_emergency(text: str) -> tuple[bool, list[str]]:
    value = text.lower()
    rules = {
        "difficulty breathing": ["difficulty breathing", "can't breathe", "cannot breathe"],
        "difficulty swallowing": ["difficulty swallowing", "cannot swallow", "can't swallow"],
        "uncontrolled bleeding": ["uncontrolled bleeding", "bleeding won't stop", "bleeding will not stop"],
        "rapid facial/neck swelling": ["rapid swelling", "neck swelling", "face swelling", "facial swelling", "face is swelling", "face is swollen"],
        "serious trauma": ["jaw fracture", "facial trauma", "knocked unconscious", "unconscious after injury"],
    }
    matched = [label for label, phrases in rules.items() if any(p in value for p in phrases)]
    return bool(matched), matched


async def _patient_ai(capability: str, actor: dict, message: str, patient_id: str | None, language: str, system_goal: str) -> dict[str, Any]:
    store = get_dental_store()
    if not store.ai_consent(actor["user_id"]):
        raise HTTPException(403, detail={"message": "AI consent is required before first use", "code": "AI_CONSENT_REQUIRED"})
    patient = await _patient_record(actor, patient_id)
    clinic_id = patient.get("clinic_id") if patient else "patient"
    _limits(actor["user_id"], clinic_id, capability)
    safe = {"domain": "dental", "language": language, "patient": {"patient_id": patient["id"]} if patient else {}}
    goal = f"{system_goal}\nUser message: {message}\nNever diagnose. Never prescribe. Never provide medicine dosage."
    result = await _uie(capability, goal, safe, DISCLAIMER_PATIENT)
    store.save_patient_ai_message(actor["user_id"], patient.get("id") if patient else None, "user", message, clinic_id)
    store.save_patient_ai_message(actor["user_id"], patient.get("id") if patient else None, "assistant", str(result["result"]), clinic_id)
    store.record_ai_usage(actor["user_id"], clinic_id, "patient", capability, False)
    return result


@router.post("/patient/chat")
async def patient_chat(body: PatientAIIn, actor=Depends(require_patient)):
    emergency, matched = _triage_emergency(body.message)
    if emergency:
        safe = _result(
            {"message": "This may be an emergency. Seek urgent medical/dental care now, especially for breathing/swallowing difficulty, uncontrolled bleeding, or rapidly increasing facial/neck swelling.", "matched_flags": matched},
            confidence=1.0,
            sources=[],
            disclaimer=DISCLAIMER_PATIENT,
            emergency=True,
        )
        return safe
    return await _patient_ai("patient_chat", actor, body.message, body.patient_id, body.language, "Explain dental information in plain language and advise contacting the clinic for personalized assessment.")


@router.post("/patient/triage")
async def patient_triage(body: PatientTriageIn, actor=Depends(require_patient)):
    emergency, matched = _triage_emergency(body.symptoms)
    if emergency:
        return _result(
            {"urgency": "emergency", "message": "Seek urgent medical/dental care now.", "matched_flags": matched},
            confidence=1.0,
            sources=[],
            disclaimer=DISCLAIMER_PATIENT,
            emergency=True,
        )
    return await _patient_ai("patient_triage", actor, body.symptoms, body.patient_id, body.language, "Provide safe symptom triage without diagnosing. Explain red flags and when to contact the dental clinic. Do not prescribe or give dosage advice.")


@router.post("/patient/treatment-explanation")
async def patient_treatment_explanation(body: PatientPlanIn, actor=Depends(require_patient)):
    return await _patient_ai("treatment_explanation", actor, body.treatment_plan, body.patient_id, body.language, "Explain the supplied dental treatment plan in simple language, including what each step is for, common questions, and what to confirm with the dentist. Do not change the plan.")


@router.post("/patient/aftercare")
async def patient_aftercare(body: PatientAftercareIn, actor=Depends(require_patient)):
    return await _patient_ai("aftercare", actor, body.treatment, body.patient_id, body.language, "Give general dental aftercare information for the supplied treatment. Do not prescribe medication or dosage. Tell the patient when to contact the clinic urgently.")


@router.get("/patient/history")
async def patient_history(actor=Depends(require_patient)):
    patient = await _patient_record(actor)
    rows = get_dental_store().patient_ai_messages(actor["user_id"], patient.get("id") if patient else None)
    return {"result": rows, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.delete("/patient/history")
async def patient_delete_history(actor=Depends(require_patient)):
    get_dental_store().delete_patient_ai_messages(actor["user_id"])
    return {"result": {"deleted": True}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.get("/patient/appointments")
async def patient_appointments(actor=Depends(require_patient)):
    patient = await _patient_record(actor)
    if not patient:
        return {"result": [], "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}
    rows = get_dental_store().appointments(patient["clinic_id"], patient["id"])
    safe = [{"id": r["id"], "starts_at": r["starts_at"], "treatment_type": r["treatment_type"], "status": r["status"]} for r in rows]
    return {"result": safe, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.get("/patient/chart")
async def patient_chart(actor=Depends(require_patient)):
    patient = await _patient_record(actor)
    if not patient:
        return {"result": [], "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}
    rows = get_dental_store().chart(patient["id"], patient["clinic_id"])
    safe = [{"tooth_fdi": r["tooth_fdi"], "status": r["status"]} for r in rows]
    return {"result": safe, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.post("/patient/device")
async def patient_device(body: DeviceIn, actor=Depends(require_patient)):
    patient = await _patient_record(actor, body.patient_id)
    clinic_id = patient.get("clinic_id") if patient else "patient"
    get_dental_store().save_patient_device(actor["user_id"], patient.get("id") if patient else None, clinic_id, body.expo_push_token)
    return {"result": {"registered": True}, "confidence": 1.0, "sources": [], "disclaimers": [DISCLAIMER_PATIENT], "request_id": str(uuid.uuid4())}


@router.post("/patient/recall")
async def patient_recall(actor=Depends(require_patient)):
    patient = await _patient_record(actor)
    if not patient:
        return _result({"message": "No linked clinic record yet.", "push_sent": False}, confidence=1.0, sources=[], disclaimer=DISCLAIMER_PATIENT)
    appointments = get_dental_store().appointments(patient["clinic_id"], patient["id"])
    overdue = [a for a in appointments if a.get("status") in {"completed", "no_show"}]
    message = "It may be time to check with your dentist about your next follow-up." if overdue else "No recall action is currently suggested from the available record."
    sent = False
    if overdue:
        sent = await _send_expo_push(get_dental_store().patient_devices(actor["user_id"]), "Dental recall reminder", message)
    return _result({"message": message, "push_sent": sent}, confidence=1.0, sources=[], disclaimer=DISCLAIMER_PATIENT)


async def _send_expo_push(tokens: list[str], title: str, body: str) -> bool:
    tokens = [t for t in tokens if t]
    if not tokens or os.getenv("EXPO_PUSH_ENABLED", "false").lower() != "true":
        return False
    payload = [{"to": token, "title": title, "body": body, "sound": "default"} for token in tokens]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://exp.host/--/api/v2/push/send", json=payload)
        return 200 <= response.status_code < 300
    except httpx.HTTPError:
        return False
