"""Separate Clinic Assistant authentication and reception-only API surface.

Assistants are not Doctor accounts. They use dedicated identity, membership and
session tables and receive only the allow-listed reception and billing-read
operations in this module. All clinical and owner-management routers continue
to require a Doctor session.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .dental_auth import hash_password, require_doctor, verify_password
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-assistant"])

ASSISTANT_SESSION_TTL = 60 * 60 * 8
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_MAX_CLIENTS = 10_000
_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


class AssistantLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AssistantCreateIn(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=12, max_length=256)


class AssistantPatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=7, max_length=40)


class AssistantAppointmentIn(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    starts_at: datetime
    treatment_type: str = Field(min_length=1, max_length=120)

    @field_validator("starts_at")
    @classmethod
    def require_future_timezone_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Appointment start time must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("Appointment start time must be in the future")
        return normalized


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _query(store, postgres_sql: str, sqlite_sql: str, params: tuple, connection=None):
    return store._execute(
        postgres_sql if store.database_url else sqlite_sql,
        params,
        connection=connection,
    )


def _check_login_rate_limit(client_ip: str) -> None:
    key = (client_ip or "unknown").strip()[:128] or "unknown"
    now = time.monotonic()
    attempts = _LOGIN_ATTEMPTS.get(key)
    if attempts is not None:
        while attempts and now - attempts[0] >= _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            _LOGIN_ATTEMPTS.pop(key, None)
            attempts = None
    if attempts is None:
        if len(_LOGIN_ATTEMPTS) >= _LOGIN_MAX_CLIENTS:
            cutoff = now - _LOGIN_WINDOW_SECONDS
            for stale_key, stale_attempts in list(_LOGIN_ATTEMPTS.items()):
                while stale_attempts and stale_attempts[0] <= cutoff:
                    stale_attempts.popleft()
                if not stale_attempts:
                    _LOGIN_ATTEMPTS.pop(stale_key, None)
                if len(_LOGIN_ATTEMPTS) < _LOGIN_MAX_CLIENTS:
                    break
        if len(_LOGIN_ATTEMPTS) >= _LOGIN_MAX_CLIENTS:
            raise HTTPException(
                503,
                detail={
                    "code": "ASSISTANT_LOGIN_CAPACITY",
                    "message": "Assistant sign-in is temporarily busy. Please try again shortly.",
                },
                headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)},
            )
        attempts = deque()
        _LOGIN_ATTEMPTS[key] = attempts
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = max(
            1,
            int(_LOGIN_WINDOW_SECONDS - (now - attempts[0])),
        )
        raise HTTPException(
            429,
            detail={
                "code": "ASSISTANT_LOGIN_RATE_LIMITED",
                "message": "Too many sign-in attempts. Please wait and try again.",
            },
            headers={"Retry-After": str(retry_after)},
        )
    attempts.append(now)


def _assistant_membership_for_login(store, email: str):
    return _query(
        store,
        "SELECT a.id,a.email,a.password_hash,a.active,m.clinic_id "
        "FROM dental_clinic_assistants a "
        "JOIN dental_clinic_assistant_memberships m ON m.assistant_id=a.id "
        "JOIN dental_clinics c ON c.id=m.clinic_id "
        "JOIN dental_organisations o ON o.id=c.organisation_id "
        "WHERE lower(a.email)=lower(%s) AND a.active=true AND m.active=true "
        "AND m.role='assistant' AND c.active=true AND o.active=true "
        "AND m.clinic_id=a.home_clinic_id LIMIT 1",
        "SELECT a.id,a.email,a.password_hash,a.active,m.clinic_id "
        "FROM dental_clinic_assistants a "
        "JOIN dental_clinic_assistant_memberships m ON m.assistant_id=a.id "
        "JOIN dental_clinics c ON c.id=m.clinic_id "
        "JOIN dental_organisations o ON o.id=c.organisation_id "
        "WHERE lower(a.email)=lower(?) AND a.active=1 AND m.active=1 "
        "AND m.role='assistant' AND c.active=1 AND o.active=1 "
        "AND m.clinic_id=a.home_clinic_id LIMIT 1",
        (email,),
    )


def login_assistant(email: str, password: str, request: Request) -> dict:
    _check_login_rate_limit(request.client.host if request.client else "unknown")
    store = get_dental_store()
    normalized_email = email.strip().lower()
    rows = _assistant_membership_for_login(store, normalized_email)
    assistant = rows[0] if rows else None
    if (
        not assistant
        or not assistant.get("active", True)
        or not verify_password(password, str(assistant.get("password_hash") or ""))
    ):
        raise HTTPException(
            401,
            detail={
                "code": "INVALID_ASSISTANT_CREDENTIALS",
                "message": "Email or password is incorrect",
            },
        )

    token = secrets.token_urlsafe(48)
    clinic_id = str(assistant["clinic_id"])
    now = datetime.now(UTC).isoformat()
    _query(
        store,
        "INSERT INTO dental_assistant_sessions "
        "(token_hash,assistant_id,clinic_id,expires_at,revoked,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        "INSERT INTO dental_assistant_sessions "
        "(token_hash,assistant_id,clinic_id,expires_at,revoked,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            _hash(token),
            str(assistant["id"]),
            clinic_id,
            int(time.time()) + ASSISTANT_SESSION_TTL,
            False,
            now,
        ),
    )
    store.audit(
        str(assistant["id"]),
        "assistant.login",
        "clinic_assistant",
        str(assistant["id"]),
        clinic_id,
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ASSISTANT_SESSION_TTL,
        "user": {
            "id": assistant["id"],
            "email": assistant["email"],
            "role": "assistant",
            "clinic_id": clinic_id,
        },
    }


async def require_assistant(
    authorization: str | None = Header(default=None),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            detail={
                "code": "ASSISTANT_AUTH_REQUIRED",
                "message": "Assistant bearer access token required",
            },
        )
    token = authorization[7:].strip()
    if len(token) < 20:
        raise HTTPException(
            401,
            detail={
                "code": "ASSISTANT_SESSION_INVALID",
                "message": "Invalid or expired assistant session",
            },
        )

    store = get_dental_store()
    rows = _query(
        store,
        "SELECT s.assistant_id,s.clinic_id,s.expires_at,s.revoked,a.email "
        "FROM dental_assistant_sessions s "
        "JOIN dental_clinic_assistants a ON a.id=s.assistant_id "
        "JOIN dental_clinic_assistant_memberships m "
        "ON m.assistant_id=s.assistant_id AND m.clinic_id=s.clinic_id "
        "JOIN dental_clinics c ON c.id=s.clinic_id "
        "JOIN dental_organisations o ON o.id=c.organisation_id "
        "WHERE s.token_hash=%s AND a.active=true AND m.active=true "
        "AND m.role='assistant' AND c.active=true AND o.active=true",
        "SELECT s.assistant_id,s.clinic_id,s.expires_at,s.revoked,a.email "
        "FROM dental_assistant_sessions s "
        "JOIN dental_clinic_assistants a ON a.id=s.assistant_id "
        "JOIN dental_clinic_assistant_memberships m "
        "ON m.assistant_id=s.assistant_id AND m.clinic_id=s.clinic_id "
        "JOIN dental_clinics c ON c.id=s.clinic_id "
        "JOIN dental_organisations o ON o.id=c.organisation_id "
        "WHERE s.token_hash=? AND a.active=1 AND m.active=1 "
        "AND m.role='assistant' AND c.active=1 AND o.active=1",
        (_hash(token),),
    )
    row = rows[0] if rows else None
    if (
        not row
        or row.get("revoked")
        or int(row.get("expires_at") or 0) < int(time.time())
    ):
        raise HTTPException(
            401,
            detail={
                "code": "ASSISTANT_SESSION_INVALID",
                "message": "Invalid or expired assistant session",
            },
        )
    return {
        "id": str(row["assistant_id"]),
        "user_id": str(row["assistant_id"]),
        "email": str(row["email"]),
        "clinic_id": str(row["clinic_id"]),
        "role": "assistant",
    }


def _require_owner_or_admin(actor: dict) -> None:
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            403,
            detail={
                "code": "ASSISTANT_ADMIN_REQUIRED",
                "message": "A clinic owner or administrator is required",
            },
        )


def _patient_contact(patient: dict) -> dict:
    return {
        "id": patient["id"],
        "name": patient["name"],
        "phone": patient["phone"],
    }


def _appointment_summary(appointment: dict, patient: dict | None) -> dict:
    return {
        "id": appointment["id"],
        "patient_id": appointment["patient_id"],
        "patient_name": patient.get("name", "") if patient else "",
        "patient_phone": patient.get("phone", "") if patient else "",
        "starts_at": appointment["starts_at"],
        "treatment_type": appointment["treatment_type"],
        "status": appointment["status"],
        "created_at": appointment["created_at"],
    }


@router.post("/auth/assistant/login")
def assistant_password_login(body: AssistantLoginIn, request: Request):
    return login_assistant(body.email, body.password, request)


@router.get("/auth/assistant/me")
def assistant_session_me(actor: Annotated[dict, Depends(require_assistant)]):
    return {
        "user": {
            "id": actor["id"],
            "email": actor["email"],
            "role": "assistant",
            "clinic_id": actor["clinic_id"],
        }
    }


@router.post("/auth/assistant/logout")
def assistant_logout(
    actor: Annotated[dict, Depends(require_assistant)],
    authorization: str | None = Header(default=None),
):
    token = authorization[7:].strip() if authorization else ""
    store = get_dental_store()
    _query(
        store,
        "UPDATE dental_assistant_sessions SET revoked=true "
        "WHERE token_hash=%s AND assistant_id=%s AND clinic_id=%s",
        "UPDATE dental_assistant_sessions SET revoked=1 "
        "WHERE token_hash=? AND assistant_id=? AND clinic_id=?",
        (_hash(token), actor["id"], actor["clinic_id"]),
    )
    store.audit(
        actor["id"],
        "assistant.logout",
        "clinic_assistant",
        actor["id"],
        actor["clinic_id"],
    )
    return {"ok": True}


@router.get("/auth/assistants")
def list_assistant_accounts(
    actor: Annotated[dict, Depends(require_doctor)],
):
    _require_owner_or_admin(actor)
    store = get_dental_store()
    assistants = _query(
        store,
        "SELECT a.id,a.email,a.active,a.created_at "
        "FROM dental_clinic_assistants a "
        "JOIN dental_clinic_assistant_memberships m ON m.assistant_id=a.id "
        "WHERE m.clinic_id=%s AND m.active=true "
        "ORDER BY lower(a.email)",
        "SELECT a.id,a.email,a.active,a.created_at "
        "FROM dental_clinic_assistants a "
        "JOIN dental_clinic_assistant_memberships m ON m.assistant_id=a.id "
        "WHERE m.clinic_id=? AND m.active=1 "
        "ORDER BY lower(a.email)",
        (actor["clinic_id"],),
    )
    store.audit(
        actor["id"],
        "assistant.directory.viewed",
        "clinic_assistant_directory",
        actor["clinic_id"],
        actor["clinic_id"],
    )
    return {
        "assistants": [
            {
                "id": item["id"],
                "email": item["email"],
                "active": bool(item["active"]),
                "created_at": item["created_at"],
                "role": "assistant",
            }
            for item in assistants
        ]
    }


@router.post("/auth/assistants", status_code=201)
def create_assistant_account(
    body: AssistantCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    _require_owner_or_admin(actor)
    store = get_dental_store()
    email = body.email.strip().lower()
    clinic_id = str(actor["clinic_id"])
    clinic_rows = _query(
        store,
        "SELECT c.id FROM dental_clinics c JOIN dental_organisations o "
        "ON o.id=c.organisation_id WHERE c.id=%s AND c.active=true AND o.active=true",
        "SELECT c.id FROM dental_clinics c JOIN dental_organisations o "
        "ON o.id=c.organisation_id WHERE c.id=? AND c.active=1 AND o.active=1",
        (clinic_id,),
    )
    if not clinic_rows:
        raise HTTPException(
            403,
            detail={
                "code": "ASSISTANT_WORKSPACE_UNAVAILABLE",
                "message": "The active clinic workspace is not available",
            },
        )
    if store.doctor_by_email(email):
        raise HTTPException(
            409,
            detail={
                "code": "ACCOUNT_EXISTS",
                "message": "An account already exists for this email",
            },
        )
    existing = _query(
        store,
        "SELECT id FROM dental_clinic_assistants WHERE lower(email)=lower(%s)",
        "SELECT id FROM dental_clinic_assistants WHERE lower(email)=lower(?)",
        (email,),
    )
    if existing:
        raise HTTPException(
            409,
            detail={
                "code": "ACCOUNT_EXISTS",
                "message": "An account already exists for this email",
            },
        )

    assistant_id = secrets.token_hex(16)
    membership_id = secrets.token_hex(16)
    created_at = datetime.now(UTC).isoformat()
    with store.transaction() as connection:
        _query(
            store,
            "INSERT INTO dental_clinic_assistants "
            "(id,email,password_hash,home_clinic_id,active,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            "INSERT INTO dental_clinic_assistants "
            "(id,email,password_hash,home_clinic_id,active,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                assistant_id,
                email,
                hash_password(body.password),
                clinic_id,
                True,
                created_at,
            ),
            connection,
        )
        _query(
            store,
            "INSERT INTO dental_clinic_assistant_memberships "
            "(id,assistant_id,clinic_id,role,active,created_at) "
            "VALUES (%s,%s,%s,'assistant',%s,%s)",
            "INSERT INTO dental_clinic_assistant_memberships "
            "(id,assistant_id,clinic_id,role,active,created_at) "
            "VALUES (?,?,?,'assistant',?,?)",
            (membership_id, assistant_id, clinic_id, True, created_at),
            connection,
        )
        store.audit(
            actor["id"],
            "assistant.created",
            "clinic_assistant",
            assistant_id,
            clinic_id,
            connection,
        )
    return {
        "id": assistant_id,
        "email": email,
        "role": "assistant",
        "clinic_id": clinic_id,
        "active": True,
    }


@router.post("/auth/assistants/{assistant_id}/deactivate")
def deactivate_assistant_account(
    assistant_id: str,
    actor: Annotated[dict, Depends(__import__("universal_intelligence_engine.dental_auth", fromlist=["require_doctor"]).require_doctor)],
):
    _require_owner_or_admin(actor)
    store = get_dental_store()
    clinic_id = str(actor["clinic_id"])
    membership = _query(
        store,
        "SELECT id FROM dental_clinic_assistant_memberships "
        "WHERE assistant_id=%s AND clinic_id=%s AND active=true",
        "SELECT id FROM dental_clinic_assistant_memberships "
        "WHERE assistant_id=? AND clinic_id=? AND active=1",
        (assistant_id, clinic_id),
    )
    if not membership:
        raise HTTPException(
            404,
            detail={
                "code": "ASSISTANT_NOT_FOUND",
                "message": "Assistant account not found in this clinic",
            },
        )
    with store.transaction() as connection:
        _query(
            store,
            "UPDATE dental_clinic_assistant_memberships SET active=false "
            "WHERE assistant_id=%s AND clinic_id=%s",
            "UPDATE dental_clinic_assistant_memberships SET active=0 "
            "WHERE assistant_id=? AND clinic_id=?",
            (assistant_id, clinic_id),
            connection,
        )
        _query(
            store,
            "UPDATE dental_clinic_assistants SET active=false WHERE id=%s",
            "UPDATE dental_clinic_assistants SET active=0 WHERE id=?",
            (assistant_id,),
            connection,
        )
        _query(
            store,
            "UPDATE dental_assistant_sessions SET revoked=true "
            "WHERE assistant_id=%s AND clinic_id=%s",
            "UPDATE dental_assistant_sessions SET revoked=1 "
            "WHERE assistant_id=? AND clinic_id=?",
            (assistant_id, clinic_id),
            connection,
        )
        store.audit(
            actor["id"],
            "assistant.deactivated",
            "clinic_assistant",
            assistant_id,
            clinic_id,
            connection,
        )
    return {
        "ok": True,
        "id": assistant_id,
        "clinic_id": clinic_id,
        "active": False,
    }


@router.get("/assistant/patients")
def list_assistant_patients(actor: Annotated[dict, Depends(require_assistant)]):
    store = get_dental_store()
    patients = [_patient_contact(item) for item in store.patients(actor["clinic_id"])]
    store.audit(
        actor["id"],
        "assistant.patient_directory.viewed",
        "patient_directory",
        actor["clinic_id"],
        actor["clinic_id"],
    )
    return {"patients": patients}


@router.get("/assistant/patients/{patient_id}")
def get_assistant_patient(
    patient_id: str,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    patient = store.get_patient(patient_id, actor["clinic_id"])
    if not patient:
        raise HTTPException(
            404,
            detail={
                "code": "PATIENT_NOT_FOUND",
                "message": "Patient not found in this clinic",
            },
        )
    store.audit(
        actor["id"],
        "assistant.patient_contact.viewed",
        "patient",
        patient_id,
        actor["clinic_id"],
    )
    return _patient_contact(patient)


@router.post("/assistant/patients", status_code=201)
def create_assistant_patient(
    body: AssistantPatientIn,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    patient = store.create_patient(
        {"name": body.name.strip(), "phone": body.phone.strip(), "age": ""},
        actor["clinic_id"],
    )
    store.audit(
        actor["id"],
        "assistant.patient_intake.created",
        "patient",
        patient["id"],
        actor["clinic_id"],
    )
    return _patient_contact(patient)


@router.get("/assistant/appointments")
def list_assistant_appointments(actor: Annotated[dict, Depends(require_assistant)]):
    store = get_dental_store()
    patients = {item["id"]: item for item in store.patients(actor["clinic_id"])}
    appointments = [
        _appointment_summary(item, patients.get(item["patient_id"]))
        for item in store.appointments(actor["clinic_id"])
    ]
    store.audit(
        actor["id"],
        "assistant.appointment_queue.viewed",
        "appointment_queue",
        actor["clinic_id"],
        actor["clinic_id"],
    )
    return {"appointments": appointments}


@router.post("/assistant/appointments", status_code=201)
def create_assistant_appointment(
    body: AssistantAppointmentIn,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    patient = store.get_patient(body.patient_id, actor["clinic_id"])
    if not patient:
        raise HTTPException(
            404,
            detail={
                "code": "PATIENT_NOT_FOUND",
                "message": "Patient not found in this clinic",
            },
        )
    try:
        appointment = store.create_appointment(
            {
                "patient_id": body.patient_id,
                "starts_at": body.starts_at.astimezone(UTC).isoformat(),
                "treatment_type": body.treatment_type.strip(),
                "note": "",
                "status": "requested",
            },
            actor["clinic_id"],
        )
    except ValueError as exc:
        raise HTTPException(
            404,
            detail={
                "code": "PATIENT_NOT_FOUND",
                "message": "Patient not found in this clinic",
            },
        ) from exc
    store.audit(
        actor["id"],
        "assistant.appointment_request.created",
        "appointment",
        appointment["id"],
        actor["clinic_id"],
    )
    return _appointment_summary(appointment, patient)


def _change_appointment_status(
    appointment_id: str,
    status: str,
    actor: dict,
) -> dict:
    store = get_dental_store()
    appointments = store.appointments(actor["clinic_id"])
    current = next(
        (item for item in appointments if str(item["id"]) == appointment_id),
        None,
    )
    if not current:
        raise HTTPException(
            404,
            detail={
                "code": "APPOINTMENT_NOT_FOUND",
                "message": "Appointment not found in this clinic",
            },
        )
    if current["status"] == status:
        patients = {item["id"]: item for item in store.patients(actor["clinic_id"])}
        return _appointment_summary(current, patients.get(current["patient_id"]))
    if current["status"] in {"completed", "cancelled", "no_show"}:
        raise HTTPException(
            409,
            detail={
                "code": "APPOINTMENT_STATE_CONFLICT",
                "message": "This appointment can no longer be changed by reception",
            },
        )
    updated = store.update_appointment(
        appointment_id,
        {"status": status},
        actor["clinic_id"],
    )
    if not updated:
        raise HTTPException(
            404,
            detail={
                "code": "APPOINTMENT_NOT_FOUND",
                "message": "Appointment not found in this clinic",
            },
        )
    patients = {item["id"]: item for item in store.patients(actor["clinic_id"])}
    store.audit(
        actor["id"],
        "assistant.appointment." + status,
        "appointment",
        appointment_id,
        actor["clinic_id"],
    )
    return _appointment_summary(updated, patients.get(updated["patient_id"]))


@router.post("/assistant/appointments/{appointment_id}/confirm")
def confirm_assistant_appointment(
    appointment_id: str,
    actor: Annotated[dict, Depends(require_assistant)],
):
    return _change_appointment_status(appointment_id, "confirmed", actor)


@router.post("/assistant/appointments/{appointment_id}/cancel")
def cancel_assistant_appointment(
    appointment_id: str,
    actor: Annotated[dict, Depends(require_assistant)],
):
    return _change_appointment_status(appointment_id, "cancelled", actor)


@router.get("/assistant/patients/{patient_id}/billing")
def get_assistant_patient_billing(
    patient_id: str,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(
            404,
            detail={
                "code": "PATIENT_NOT_FOUND",
                "message": "Patient not found in this clinic",
            },
        )
    invoices = []
    for invoice in store.invoices(actor["clinic_id"], patient_id):
        invoices.append(
            {
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
                "due_date": invoice["due_date"],
                "issued_at": invoice["issued_at"],
                "receipts": [
                    {
                        "receipt_number": receipt["receipt_number"],
                        "amount_paise": int(receipt["amount_paise"]),
                        "method": receipt["method"],
                        "paid_at": receipt["paid_at"],
                    }
                    for receipt in invoice["receipts"]
                ],
            }
        )
    store.audit(
        actor["id"],
        "assistant.billing_summary.viewed",
        "patient_billing",
        patient_id,
        actor["clinic_id"],
    )
    return {"invoices": invoices}
