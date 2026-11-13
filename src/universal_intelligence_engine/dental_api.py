"""Dental application API with portable persistence and UIE adapter."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .dental_auth import (
    hash_password,
    login_password,
    logout_doctor,
    require_doctor,
    switch_doctor_workspace,
)
from .dental_billing import create_order as create_membership_order_impl
from .dental_billing import handle_webhook as handle_membership_webhook
from .dental_billing import plans as membership_plans
from .dental_store import get_dental_store
from .scano_adapter import get_scano_adapter
from .whatsapp_adapter import get_whatsapp_adapter

router = APIRouter(prefix="/v1/dental", tags=["dental"])

# Public guest intake is intentionally bounded even before an edge/WAF policy is
# configured. This in-process sliding window is suitable for the current
# single-instance staging service; production horizontal scaling must add a
# shared rate limiter or edge-level rule before increasing instance count.
_PUBLIC_APPOINTMENT_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
_PUBLIC_APPOINTMENT_WINDOW_SECONDS = 300
_PUBLIC_APPOINTMENT_MAX_ATTEMPTS = 8
_PUBLIC_APPOINTMENT_MAX_CLIENTS = 10_000


def _public_appointment_client_ip(request: Request) -> str:
    """Use Cloudflare's edge-supplied client IP on Render, not the proxy socket IP.

    Render routes public web-service traffic through Cloudflare. CF-Connecting-IP
    is set by Cloudflare for origin traffic and has a single-IP format; do not
    trust caller-supplied X-Forwarded-For chains as a rate-limit identity.
    """
    forwarded = str(request.headers.get("cf-connecting-ip") or "").strip()
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(request.client.host if request.client else "unknown")


def _check_public_appointment_rate_limit(client_ip: str) -> None:
    key = (client_ip or "unknown").strip()[:128] or "unknown"
    now = time.monotonic()
    attempts = _PUBLIC_APPOINTMENT_ATTEMPTS.get(key)
    if attempts is not None:
        while attempts and now - attempts[0] >= _PUBLIC_APPOINTMENT_WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            _PUBLIC_APPOINTMENT_ATTEMPTS.pop(key, None)
            attempts = None

    if attempts is None:
        if len(_PUBLIC_APPOINTMENT_ATTEMPTS) >= _PUBLIC_APPOINTMENT_MAX_CLIENTS:
            cutoff = now - _PUBLIC_APPOINTMENT_WINDOW_SECONDS
            for stale_key, stale_attempts in list(_PUBLIC_APPOINTMENT_ATTEMPTS.items()):
                while stale_attempts and stale_attempts[0] <= cutoff:
                    stale_attempts.popleft()
                if not stale_attempts:
                    _PUBLIC_APPOINTMENT_ATTEMPTS.pop(stale_key, None)
                if len(_PUBLIC_APPOINTMENT_ATTEMPTS) < _PUBLIC_APPOINTMENT_MAX_CLIENTS:
                    break
        if len(_PUBLIC_APPOINTMENT_ATTEMPTS) >= _PUBLIC_APPOINTMENT_MAX_CLIENTS:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PUBLIC_APPOINTMENT_CAPACITY",
                    "message": "Public booking is temporarily busy. Please try again shortly.",
                },
                headers={"Retry-After": str(_PUBLIC_APPOINTMENT_WINDOW_SECONDS)},
            )
        attempts = deque()
        _PUBLIC_APPOINTMENT_ATTEMPTS[key] = attempts

    if len(attempts) >= _PUBLIC_APPOINTMENT_MAX_ATTEMPTS:
        retry_after = max(
            1,
            int(_PUBLIC_APPOINTMENT_WINDOW_SECONDS - (now - attempts[0])),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PUBLIC_APPOINTMENT_RATE_LIMITED",
                "message": "Too many appointment requests. Please wait and try again.",
            },
            headers={"Retry-After": str(retry_after)},
        )
    attempts.append(now)


class DoctorPasswordLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)



class WorkspaceSwitchIn(BaseModel):
    clinic_id: str = Field(min_length=1, max_length=128)


class OrganisationCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    organisation_type: str = Field(pattern=r"^(clinic|hospital|group)$")
    workspace_name: str | None = Field(default=None, min_length=1, max_length=200)
    clinic_type: str = Field(
        default="clinic", pattern=r"^(clinic|branch|hospital_department)$"
    )


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    clinic_type: str = Field(pattern=r"^(clinic|branch|hospital_department)$")
    parent_clinic_id: str | None = Field(default=None, max_length=128)


class DoctorMembershipCreateIn(BaseModel):
    doctor_id: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern=r"^(owner|admin|doctor|dentist)$")


class DoctorMembershipUpdateIn(BaseModel):
    active: bool


class DoctorAccountCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="doctor", pattern=r"^(doctor|dentist|admin)$")


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
    status: str | None = Field(
        default=None,
        pattern=r"^(requested|confirmed|scheduled|completed|cancelled|rescheduled|no_show)$",
    )
    starts_at: str | None = Field(default=None, min_length=5, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class PublicAppointmentIn(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128)
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=7, max_length=40)
    starts_at: datetime
    treatment_type: Literal[
        "Dental check-up",
        "Cleaning & polishing",
        "Tooth pain consultation",
        "Root canal treatment",
        "Braces & aligners",
        "Dental implants",
        "Other dental concern",
    ] = "Dental check-up"
    intelligence_request: Literal[False] = False
    website: str = Field(default="", max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 16:
            raise ValueError("A secure idempotency key is required")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Enter your name")
        return normalized

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        normalized = value.strip()
        digits = "".join(character for character in normalized if character.isdigit())
        indian_mobile = (
            len(digits) == 10 and digits[0] in "6789"
        ) or (
            len(digits) == 12 and digits.startswith("91") and digits[2] in "6789"
        )
        if not indian_mobile:
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        if any(character not in "+0123456789 ()-" for character in normalized):
            raise ValueError("Enter a valid contact number")
        return normalized

    @field_validator("starts_at")
    @classmethod
    def require_future_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Appointment time must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("Appointment time must be in the future")
        return normalized


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
async def get_membership(doctor: Annotated[dict, Depends(require_doctor)]):
    clinic_id = doctor.get("clinic_id", "dr-pranali")
    master = clinic_id == os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()
    if master:
        return {
            "clinic_id": clinic_id,
            "plan_id": "master",
            "name": "Dr. Pranali Master",
            "status": "active",
            "price_inr": 0,
            "period": "forever",
            "master": True,
        }
    store = get_dental_store()
    rows = store._execute(
        "SELECT * FROM dental_memberships WHERE clinic_id=%s ORDER BY created_at DESC LIMIT 1"
        if store.database_url
        else "SELECT * FROM dental_memberships WHERE clinic_id=? ORDER BY created_at DESC LIMIT 1",
        (clinic_id,),
    )
    return {
        "clinic_id": clinic_id,
        "membership": rows[0] if rows else None,
        "master": False,
    }


@router.post("/billing/order")
async def create_membership_order(
    body: MembershipOrderIn, doctor: Annotated[dict, Depends(require_doctor)]
):
    clinic_id = doctor.get("clinic_id")
    if not clinic_id:
        raise HTTPException(400, "Clinic context is required")
    return await create_membership_order_impl(
        clinic_id, body.plan_id, doctor.get("id", "")
    )


@router.post("/billing/razorpay/webhook")
async def membership_webhook(
    request: Request, x_razorpay_signature: str = Header(default="")
):
    return handle_membership_webhook(await request.body(), x_razorpay_signature)


@router.post("/auth/password/login")
async def doctor_password_login(body: DoctorPasswordLoginIn, request: Request):
    return await login_password(body.email, body.password, request)



@router.post("/auth/workspace/switch")
async def doctor_workspace_switch(
    body: WorkspaceSwitchIn,
    doctor: Annotated[dict, Depends(require_doctor)],
    authorization: str | None = Header(default=None),
):
    return await switch_doctor_workspace(
        authorization, body.clinic_id, current_user=doctor
    )


def _require_organisation_admin(store, actor: dict, organisation_id: str) -> dict:
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ORGANISATION_ADMIN_REQUIRED",
                "message": "An organisation owner or administrator is required",
            },
        )
    authorised_organisations = {
        str(item["id"]) for item in store.organisations_for_doctor(actor["id"])
    }
    if str(organisation_id) not in authorised_organisations:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ORGANISATION_ACCESS_DENIED",
                "message": "You cannot administer a different organisation",
            },
        )
    active_workspace = next(
        (
            workspace
            for workspace in store.workspaces_for_doctor(actor["id"])
            if str(workspace["id"]) == str(actor["clinic_id"])
        ),
        None,
    )
    if not active_workspace or active_workspace["organisation_id"] != organisation_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ORGANISATION_ACCESS_DENIED",
                "message": "You cannot administer a different organisation",
            },
        )
    return active_workspace


def _get_workspace_record(store, clinic_id: str) -> dict | None:
    sql = (
        "SELECT id, organisation_id, name, clinic_type, parent_clinic_id, active, created_at "
        "FROM dental_clinics WHERE id=%s"
        if store.database_url
        else "SELECT id, organisation_id, name, clinic_type, parent_clinic_id, active, created_at "
        "FROM dental_clinics WHERE id=?"
    )
    rows = store._execute(sql, (clinic_id,))
    return rows[0] if rows else None


def _require_doctor_same_organisation(
    store, doctor: dict, organisation_id: str
) -> None:
    # A doctor can be assigned within the organisation of their original
    # workspace even before their first secondary membership exists. An
    # organisation creator is the deliberate exception to that anchor: an
    # active owner membership plus organisation.created audit provenance also
    # authorises them in the organisation they created.
    home_clinic_id = str(doctor.get("clinic_id") or "")
    home_workspace = (
        _get_workspace_record(store, home_clinic_id) if home_clinic_id else None
    )
    if home_workspace and str(home_workspace["organisation_id"]) == str(organisation_id):
        return

    authorised_organisations = {
        str(item["id"]) for item in store.organisations_for_doctor(doctor["id"])
    }
    if str(organisation_id) not in authorised_organisations:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DOCTOR_ORGANISATION_MISMATCH",
                "message": "Doctors can only be assigned within their own organisation",
            },
        )


@router.get("/auth/workspaces")
async def list_doctor_workspaces(
    doctor: Annotated[dict, Depends(require_doctor)],
):
    return {"workspaces": get_dental_store().workspaces_for_doctor(doctor["id"])}


@router.post("/organisations", status_code=201)
async def create_organisation(
    body: OrganisationCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ORGANISATION_ADMIN_REQUIRED",
                "message": "An organisation owner or administrator is required",
            },
        )
    store = get_dental_store()
    organisation_id = secrets.token_hex(16)
    clinic_id = secrets.token_hex(16)
    with store.transaction() as connection:
        organisation = store.create_organisation(
            {
                "id": organisation_id,
                "name": body.name.strip(),
                "organisation_type": body.organisation_type,
            },
            connection=connection,
        )
        workspace = store.create_clinic(
            {
                "id": clinic_id,
                "organisation_id": organisation_id,
                "name": (body.workspace_name or body.name).strip(),
                "clinic_type": body.clinic_type,
            },
            connection=connection,
        )
        membership = store.create_doctor_membership(
            actor["id"], clinic_id, "owner", connection=connection
        )
        store.audit(
            actor["id"],
            "organisation.created",
            "organisation",
            organisation_id,
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
        store.audit(
            actor["id"],
            "workspace.created",
            "clinic",
            clinic_id,
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return {
        "organisation": organisation,
        "workspace": workspace,
        "membership": membership,
    }


@router.post("/organisations/{organisation_id}/workspaces", status_code=201)
async def create_organisation_workspace(
    organisation_id: str,
    body: WorkspaceCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    _require_organisation_admin(store, actor, organisation_id)
    if body.parent_clinic_id:
        parent = _get_workspace_record(store, body.parent_clinic_id)
        if not parent or parent["organisation_id"] != organisation_id or not parent["active"]:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PARENT_WORKSPACE_NOT_FOUND",
                    "message": "Parent workspace not found in this organisation",
                },
            )
    with store.transaction() as connection:
        workspace = store.create_clinic(
            {
                "id": secrets.token_hex(16),
                "organisation_id": organisation_id,
                "name": body.name.strip(),
                "clinic_type": body.clinic_type,
                "parent_clinic_id": body.parent_clinic_id,
            },
            connection=connection,
        )
        # Keep the creating organisation administrator authorised in the new
        # workspace so it can be selected and managed immediately.
        membership = store.create_doctor_membership(
            actor["id"], workspace["id"], actor["role"], connection=connection
        )
        store.audit(
            actor["id"],
            "workspace.created",
            "clinic",
            workspace["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
        store.audit(
            actor["id"],
            "membership.created",
            "doctor_membership",
            membership["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return workspace


@router.get("/organisations/{organisation_id}/workspaces")
async def list_organisation_workspaces(
    organisation_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    _require_organisation_admin(store, actor, organisation_id)
    return {"workspaces": store.workspaces_for_organisation(organisation_id)}


@router.post("/workspaces/{clinic_id}/memberships", status_code=201)
async def create_workspace_membership(
    clinic_id: str,
    body: DoctorMembershipCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    target_workspace = _get_workspace_record(store, clinic_id)
    if not target_workspace or not target_workspace["active"]:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
        )
    _require_organisation_admin(store, actor, target_workspace["organisation_id"])
    target_doctor = store.doctor_by_id(body.doctor_id)
    if not target_doctor:
        raise HTTPException(
            status_code=404,
            detail={"code": "DOCTOR_NOT_FOUND", "message": "Doctor account not found"},
        )
    _require_doctor_same_organisation(
        store, target_doctor, target_workspace["organisation_id"]
    )
    if body.role == "owner" and actor.get("role") != "owner":
        raise HTTPException(
            status_code=403,
            detail={"code": "OWNER_ROLE_REQUIRED", "message": "Only an owner can assign an owner role"},
        )
    if store.doctor_membership(body.doctor_id, clinic_id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MEMBERSHIP_EXISTS",
                "message": "Doctor already has a membership in this workspace",
            },
        )
    with store.transaction() as connection:
        membership = store.create_doctor_membership(
            body.doctor_id, clinic_id, body.role, connection=connection
        )
        store.audit(
            actor["id"],
            "membership.created",
            "doctor_membership",
            membership["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return membership


@router.patch("/workspaces/{clinic_id}/memberships/{doctor_id}")
async def update_workspace_membership(
    clinic_id: str,
    doctor_id: str,
    body: DoctorMembershipUpdateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    target_workspace = _get_workspace_record(store, clinic_id)
    if not target_workspace or not target_workspace["active"]:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
        )
    _require_organisation_admin(store, actor, target_workspace["organisation_id"])
    membership = store.doctor_membership(doctor_id, clinic_id)
    if not membership:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MEMBERSHIP_NOT_FOUND",
                "message": "Doctor membership not found in this workspace",
            },
        )
    target_doctor = store.doctor_by_id(doctor_id)
    if not target_doctor:
        raise HTTPException(
            status_code=404,
            detail={"code": "DOCTOR_NOT_FOUND", "message": "Doctor account not found"},
        )
    _require_doctor_same_organisation(
        store, target_doctor, target_workspace["organisation_id"]
    )
    if doctor_id == actor["id"] and not body.active:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CANNOT_DEACTIVATE_SELF",
                "message": "You cannot deactivate your own workspace membership",
            },
        )
    if membership["role"] == "owner" and actor.get("role") != "owner":
        raise HTTPException(
            status_code=403,
            detail={"code": "OWNER_ROLE_REQUIRED", "message": "Only an owner can change an owner membership"},
        )
    with store.transaction() as connection:
        store.set_doctor_membership_active(
            doctor_id, clinic_id, body.active, connection=connection
        )
        store.audit(
            actor["id"],
            "membership.activated" if body.active else "membership.deactivated",
            "doctor_membership",
            membership["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return {
        "doctor_id": doctor_id,
        "clinic_id": clinic_id,
        "active": body.active,
    }


@router.post("/auth/doctors")
async def create_doctor_account(
    body: DoctorAccountCreateIn, actor: Annotated[dict, Depends(require_doctor)]
):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "Clinic administrator required",
            },
        )
    email = body.email.strip().lower()
    store = get_dental_store()
    if store.doctor_by_email(email):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_EXISTS",
                "message": "An account already exists for this email",
            },
        )
    with store.transaction() as connection:
        doctor = store.create_doctor(
            {
                "id": secrets.token_hex(16),
                "email": email,
                "password_hash": hash_password(body.password),
                "role": body.role,
                "clinic_id": actor["clinic_id"],
            },
            connection=connection,
        )
        membership = store.create_doctor_membership(
            doctor["id"], actor["clinic_id"], body.role, connection=connection
        )
        store.audit(
            actor["id"],
            "membership.created",
            "doctor_membership",
            membership["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return {
        "id": doctor["id"],
        "email": doctor["email"],
        "role": membership["role"],
        "clinic_id": actor["clinic_id"],
        "active": True,
    }


@router.post("/auth/doctors/{doctor_id}/deactivate")
async def deactivate_doctor_account(
    doctor_id: str, actor: Annotated[dict, Depends(require_doctor)]
):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "Clinic administrator required",
            },
        )
    store = get_dental_store()
    target = store.doctor_by_id(doctor_id)
    membership = store.doctor_membership(doctor_id, actor["clinic_id"])
    if not target or not membership:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ACCOUNT_NOT_FOUND",
                "message": "Doctor account not found in this workspace",
            },
        )
    actor_workspace = _get_workspace_record(store, actor["clinic_id"])
    if not actor_workspace or not actor_workspace["active"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ORGANISATION_ACCESS_DENIED",
                "message": "The active workspace is not available",
            },
        )
    _require_doctor_same_organisation(
        store, target, actor_workspace["organisation_id"]
    )
    if doctor_id == actor["id"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CANNOT_DEACTIVATE_SELF",
                "message": "You cannot deactivate your own workspace membership",
            },
        )
    if membership["role"] == "owner" and actor.get("role") != "owner":
        raise HTTPException(
            status_code=403,
            detail={"code": "OWNER_ROLE_REQUIRED", "message": "Only an owner can deactivate an owner membership"},
        )
    with store.transaction() as connection:
        store.set_doctor_membership_active(
            doctor_id, actor["clinic_id"], False, connection=connection
        )
        store.audit(
            actor["id"],
            "membership.deactivated",
            "doctor_membership",
            membership["id"],
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return {
        "ok": True,
        "id": doctor_id,
        "clinic_id": actor["clinic_id"],
        "active": False,
    }


@router.get("/auth/me")
async def doctor_session_me(doctor: Annotated[dict, Depends(require_doctor)]):
    return {
        "user": {
            "id": doctor["id"],
            "email": doctor["email"],
            "role": doctor["role"],
            "clinic_id": doctor["clinic_id"],
        }
    }


@router.post("/auth/logout")
async def doctor_logout(
    doctor: Annotated[dict, Depends(require_doctor)],
    authorization: str | None = Header(default=None),
):
    await logout_doctor(authorization)
    return {"ok": True}


@router.get("/health")
async def dental_health():
    store = get_dental_store()
    return {
        "status": "ok",
        "service": "dental-api",
        "database": store.backend,
        "uie_configured": bool(os.getenv("UIE_API_URL") and os.getenv("UIE_API_KEY")),
    }


def _clinic_id(actor: dict) -> str:
    clinic_id = str(actor.get("clinic_id") or "").strip()
    if not clinic_id:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Doctor clinic context is required",
                "code": "CLINIC_CONTEXT_REQUIRED",
            },
        )
    return clinic_id


@router.get("/patients")
async def list_patients(actor: Annotated[dict, Depends(require_doctor)]):
    return get_dental_store().patients(_clinic_id(actor))


@router.post("/patients")
async def create_patient(
    body: PatientIn, actor: Annotated[dict, Depends(require_doctor)]
):
    store = get_dental_store()
    item = store.create_patient(body.model_dump(), _clinic_id(actor))
    store.audit(actor.get("user_id"), "create", "patient", item["id"], clinic_id=_clinic_id(actor))
    return item


@router.post("/public/appointments")
async def create_public_appointment(body: PublicAppointmentIn, request: Request):
    """Create a privacy-minimised, idempotent guest request for the master clinic.

    No patient account, clinical note, AI request, patient ID or appointment ID
    is exposed. A public reference is a receipt only; it is not an access token.
    """
    _check_public_appointment_rate_limit(_public_appointment_client_ip(request))
    if body.website.strip():
        # Honeypot submissions receive a neutral response but never persist.
        return {
            "status": "received",
            "message": "Your request has been received. The clinic team will contact you.",
        }

    store = get_dental_store()
    master_clinic = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"
    phone_digits = "".join(character for character in body.phone if character.isdigit())
    starts_at_utc = body.starts_at.astimezone(UTC).isoformat()
    payload = {
        "name": body.name,
        "phone": phone_digits,
        "starts_at": starts_at_utc,
        "treatment_type": body.treatment_type,
    }
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    idempotency_hash = hashlib.sha256(body.idempotency_key.encode("utf-8")).hexdigest()
    phone_hash = hashlib.sha256(phone_digits.encode("utf-8")).hexdigest()
    now = datetime.now(UTC).isoformat()
    public_reference = "ORL-" + secrets.token_hex(8).upper()

    with store.transaction() as connection:
        clinic_rows = store._execute(
            "SELECT id FROM dental_clinics WHERE id=%s AND active=true"
            if store.database_url
            else "SELECT id FROM dental_clinics WHERE id=? AND active=1",
            (master_clinic,),
            connection=connection,
        )
        if not clinic_rows:
            raise HTTPException(
                503,
                detail={
                    "code": "PUBLIC_BOOKING_UNAVAILABLE",
                    "message": "Online appointment requests are temporarily unavailable. Please call the clinic.",
                },
            )

        inserted = store._execute(
            "INSERT INTO dental_public_booking_requests("
            "id,clinic_id,idempotency_key_hash,payload_hash,phone_hash,starts_at_utc,public_reference,created_at"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id"
            if store.database_url
            else "INSERT INTO dental_public_booking_requests("
            "id,clinic_id,idempotency_key_hash,payload_hash,phone_hash,starts_at_utc,public_reference,created_at"
            ") VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id",
            (
                secrets.token_hex(16),
                master_clinic,
                idempotency_hash,
                payload_hash,
                phone_hash,
                starts_at_utc,
                public_reference,
                now,
            ),
            connection=connection,
        )
        if not inserted:
            existing_key = store._execute(
                "SELECT payload_hash,public_reference,appointment_id "
                "FROM dental_public_booking_requests WHERE clinic_id=%s AND idempotency_key_hash=%s"
                if store.database_url
                else "SELECT payload_hash,public_reference,appointment_id "
                "FROM dental_public_booking_requests WHERE clinic_id=? AND idempotency_key_hash=?",
                (master_clinic, idempotency_hash),
                connection=connection,
            )
            if existing_key:
                existing = existing_key[0]
                if existing["payload_hash"] != payload_hash or not existing["appointment_id"]:
                    raise HTTPException(
                        409,
                        detail={
                            "code": "IDEMPOTENCY_KEY_REUSED",
                            "message": "This request key was already used. Refresh the form before sending a changed request.",
                        },
                    )
                return {
                    "status": "already_received",
                    "message": "This appointment request was already received. The clinic will contact you to confirm.",
                    "reference": existing["public_reference"],
                }

            duplicate = store._execute(
                "SELECT id FROM dental_public_booking_requests "
                "WHERE clinic_id=%s AND phone_hash=%s AND starts_at_utc=%s"
                if store.database_url
                else "SELECT id FROM dental_public_booking_requests "
                "WHERE clinic_id=? AND phone_hash=? AND starts_at_utc=?",
                (master_clinic, phone_hash, starts_at_utc),
                connection=connection,
            )
            if duplicate:
                raise HTTPException(
                    409,
                    detail={
                        "code": "DUPLICATE_APPOINTMENT_REQUEST",
                        "message": "A request for this contact number and time was already received. Please choose another time or call the clinic.",
                    },
                )
            raise HTTPException(
                409,
                detail={
                    "code": "BOOKING_REQUEST_CONFLICT",
                    "message": "We could not safely confirm this request. Please refresh and try again.",
                },
            )

        patient = store.create_patient(
            {"name": body.name, "phone": body.phone, "age": ""},
            master_clinic,
            connection=connection,
        )
        appointment = store.create_appointment(
            {
                "patient_id": patient["id"],
                "starts_at": starts_at_utc,
                "treatment_type": body.treatment_type,
                "note": "",
                "status": "requested",
            },
            master_clinic,
            connection=connection,
        )
        store._execute(
            "UPDATE dental_public_booking_requests SET patient_id=%s,appointment_id=%s "
            "WHERE clinic_id=%s AND idempotency_key_hash=%s"
            if store.database_url
            else "UPDATE dental_public_booking_requests SET patient_id=?,appointment_id=? "
            "WHERE clinic_id=? AND idempotency_key_hash=?",
            (patient["id"], appointment["id"], master_clinic, idempotency_hash),
            connection=connection,
        )
        store.audit(
            "public-intake",
            "request",
            "appointment",
            appointment["id"],
            clinic_id=master_clinic,
            connection=connection,
        )

    return {
        "status": "requested",
        "message": "Your appointment request has been received. The clinic team will contact you to confirm.",
        "reference": public_reference,
    }


@router.post("/scans/screen")
async def screen_scan(
    body: ScanScreenIn, actor: Annotated[dict, Depends(require_doctor)]
):
    store = get_dental_store()
    clinic_id = _clinic_id(actor)
    if body.patient_id and not store.get_patient(body.patient_id, clinic_id):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Patient is not accessible from this clinic",
                "code": "PATIENT_CLINIC_ACCESS_DENIED",
            },
        )
    adapter = get_scano_adapter()
    try:
        result = await adapter.screen(
            {
                "patient_id": body.patient_id,
                "scan_reference": body.scan_reference,
                "scan_type": body.scan_type,
                "metadata": body.metadata,
            }
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="Scan screening is temporarily unavailable."
        ) from exc
    store.audit(actor.get("user_id"), "screen", "scan", body.patient_id, clinic_id=clinic_id)
    return result


@router.post("/whatsapp/template")
async def send_whatsapp_template(
    body: WhatsAppTemplateIn, actor: Annotated[dict, Depends(require_doctor)]
):
    adapter = get_whatsapp_adapter()
    try:
        result = await adapter.send_template(
            body.to.strip(),
            body.template_name.strip(),
            body.language_code.strip(),
            body.components,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="WhatsApp delivery is temporarily unavailable."
        ) from exc
    get_dental_store().audit(actor.get("user_id"), "send", "whatsapp", body.to.strip(), clinic_id=_clinic_id(actor))
    return result


@router.get("/appointments")
async def list_appointments(actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    clinic_id = _clinic_id(actor)
    patients = {p["id"]: p for p in store.patients(clinic_id)}
    return [
        {
            **appointment,
            "patient_name": patients.get(appointment["patient_id"], {}).get("name", ""),
            "patient_phone": patients.get(appointment["patient_id"], {}).get(
                "phone", ""
            ),
        }
        for appointment in store.appointments(clinic_id)
    ]


@router.patch("/appointments/{appointment_id}")
async def update_appointment(
    appointment_id: str,
    body: AppointmentUpdateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(
            status_code=400, detail="At least one appointment field is required"
        )
    store = get_dental_store()
    item = store.update_appointment(appointment_id, changes, _clinic_id(actor))
    if not item:
        raise HTTPException(status_code=404, detail="Appointment not found")
    store.audit(actor.get("user_id"), "update", "appointment", appointment_id, clinic_id=_clinic_id(actor))
    return item


@router.post("/appointments")
async def create_appointment(
    body: AppointmentIn, actor: Annotated[dict, Depends(require_doctor)]
):
    store = get_dental_store()
    clinic_id = _clinic_id(actor)
    if not store.get_patient(body.patient_id, clinic_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    item = store.create_appointment(body.model_dump(), clinic_id)
    store.audit(actor.get("user_id"), "create", "appointment", item["id"], clinic_id=clinic_id)
    return item


@router.get("/patients/{patient_id}/chart")
async def get_chart(patient_id: str, actor: Annotated[dict, Depends(require_doctor)]):
    return get_dental_store().chart(patient_id, _clinic_id(actor))


@router.post("/patients/{patient_id}/chart")
async def save_chart(
    patient_id: str, body: ChartEntryIn, actor: Annotated[dict, Depends(require_doctor)]
):
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    store = get_dental_store()
    clinic_id = _clinic_id(actor)
    if not store.get_patient(patient_id, clinic_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    item = store.save_chart(body.model_dump(), clinic_id)
    store.audit(actor.get("user_id"), "write", "dental_chart", item["id"], clinic_id=clinic_id)
    return item


@router.get("/patients/{patient_id}/periodontogram")
async def get_periodontogram(
    patient_id: str, actor: Annotated[dict, Depends(require_doctor)]
):
    return get_dental_store().periodontogram(patient_id, _clinic_id(actor))


@router.post("/patients/{patient_id}/periodontogram")
async def save_periodontogram(
    patient_id: str,
    body: PeriodontalEntryIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    store = get_dental_store()
    clinic_id = _clinic_id(actor)
    if not store.get_patient(patient_id, clinic_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    item = store.save_periodontogram(body.model_dump(), clinic_id)
    store.audit(
        actor.get("user_id"),
        "write",
        "periodontogram",
        item["id"],
        clinic_id=clinic_id,
    )
    return item


@router.post("/intelligence/run")
async def run_dental_intelligence(
    body: IntelligenceIn, actor: Annotated[dict, Depends(require_doctor)]
):
    clinic_id = str(actor.get("clinic_id") or "").strip()
    if not clinic_id:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Doctor clinic context is required",
                "code": "CLINIC_CONTEXT_REQUIRED",
            },
        )

    patient = None
    if body.patient_id:
        patient = get_dental_store().get_patient(body.patient_id, clinic_id)
        if not patient:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Patient is not accessible from this clinic",
                    "code": "PATIENT_CLINIC_ACCESS_DENIED",
                },
            )

    uie_url, uie_key = os.getenv("UIE_API_URL"), os.getenv("UIE_API_KEY")
    if not uie_url or not uie_key:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "UIE bridge is not configured",
                "code": "UIE_BACKEND_NOT_CONFIGURED",
            },
        )

    # Minimum necessary context is built server-side from the authenticated clinic.
    # The app never sends patient phone/name/free-form chart notes to UIE.
    safe_context: dict[str, Any] = {"domain": "dental"}
    if patient:
        store = get_dental_store()
        safe_context["patient"] = {
            "patient_id": patient["id"],
            "age": patient.get("age", ""),
        }
        safe_context["dental_chart"] = [
            {"tooth_fdi": row["tooth_fdi"], "status": row["status"]}
            for row in store.chart(patient["id"], clinic_id)
        ]
        safe_context["periodontogram"] = [
            {"tooth_fdi": row["tooth_fdi"], "measurements": row.get("measurements", {})}
            for row in store.periodontogram(patient["id"], clinic_id)
        ]
        safe_context["appointments"] = [
            {
                "starts_at": row["starts_at"],
                "treatment_type": row["treatment_type"],
                "status": row["status"],
            }
            for row in store.appointments(clinic_id, patient["id"])
        ]
        safe_context["patient_id"] = patient["id"]

    payload = {
        "goal": body.goal.strip(),
        "language": body.language,
        "locale": body.locale,
        "context": safe_context,
        "constraints": {
            "require_sources": True,
            "human_approval_required": True,
            "do_not_write_records": True,
            "domain": "dental",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{uie_url.rstrip('/')}/v1/intelligence/run",
                headers={
                    "Authorization": f"Bearer {uie_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "message": "Clinical AI took too long to respond. Please retry.",
                "code": "UIE_TIMEOUT",
            },
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Clinical AI service could not be reached. Please retry.",
                "code": "UIE_UNREACHABLE",
            },
        ) from exc

    if response.status_code >= 400:
        try:
            upstream = response.json()
        except ValueError:
            upstream = None
        detail = upstream.get("detail") if isinstance(upstream, dict) else None
        if isinstance(detail, dict) and detail.get("code") == "PROVIDER_NOT_CONFIGURED":
            raise HTTPException(status_code=503, detail=detail)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Clinical AI service returned an error. Please retry.",
                "code": "UIE_REQUEST_FAILED",
            },
        )

    result = response.json()
    store = get_dental_store()
    if body.patient_id:
        store.record_ai(body.patient_id, body.goal, result, clinic_id)
    store.audit(actor.get("id"), "run", "dental_intelligence", body.patient_id, clinic_id=clinic_id)
    return result
