"""Separate Clinic Assistant authentication and least-privilege API.

Assistants can manage reception intake, appointment confirmation, and limited
billing collection. They cannot use Doctor endpoints or access clinical data.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from .dental_auth import hash_password, require_doctor, verify_password
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-clinic-assistant"])

ASSISTANT_SESSION_TTL = 8 * 60 * 60
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_MAX_CLIENTS = 10_000
_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _envelope(result):
    return {
        "result": result,
        "confidence": 1.0,
        "sources": [],
        "request_id": str(uuid.uuid4()),
    }


def _query(store, postgres_sql: str, sqlite_sql: str, params: tuple, connection=None):
    sql = postgres_sql if store.database_url else sqlite_sql
    return store._execute(sql, params, connection=connection)


def _assistant_login_client_ip(request: Request) -> str:
    """Use Render's Cloudflare-validated client IP, not the shared proxy socket."""
    forwarded = str(request.headers.get("cf-connecting-ip") or "").strip()
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(request.client.host if request.client else "unknown")


def _check_login_rate_limit(request: Request) -> None:
    key = (_assistant_login_client_ip(request) or "unknown").strip()[:128] or "unknown"
    now = time.monotonic()
    attempts = _LOGIN_ATTEMPTS.get(key)
    if attempts is not None:
        while attempts and now - attempts[0] >= _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            _LOGIN_ATTEMPTS.pop(key, None)
            attempts = None

    # Bound memory use if an attacker rotates source IPs. This remains a
    # single-instance guard; production horizontal scaling needs shared storage
    # or a trusted edge/WAF rate-limit rule.
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
                    "message": "Sign-in is temporarily busy. Please try again shortly.",
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
                "message": "Too many sign-in attempts. Please wait before trying again.",
            },
            headers={"Retry-After": str(retry_after)},
        )
    attempts.append(now)

class AssistantLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AssistantCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("Enter a valid assistant email address")
        return normalized


class AssistantUpdateIn(BaseModel):
    active: bool


class AssistantPatientCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=7, max_length=40)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Patient name must contain at least two non-space characters")
        return normalized

    @field_validator("phone")
    @classmethod
    def trim_phone(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def validate_indian_mobile(cls, value: str) -> str:
        digits = "".join(character for character in value if character.isdigit())
        valid = (len(digits) == 10 and digits[0] in "6789") or (
            len(digits) == 12 and digits.startswith("91") and digits[2] in "6789"
        )
        if not valid:
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return value


class AssistantAppointmentUpdateIn(BaseModel):
    status: Literal["confirmed", "cancelled", "rescheduled"] | None = None
    starts_at: datetime | None = None

    @model_validator(mode="after")
    def require_reschedule_time(self):
        if self.status == "rescheduled" and self.starts_at is None:
            raise ValueError("A new timezone-aware time is required to reschedule")
        if self.starts_at is not None and self.status not in (None, "rescheduled"):
            raise ValueError("A new time can only be supplied for a reschedule")
        return self

    @field_validator("starts_at")
    @classmethod
    def require_future_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Appointment time must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("Appointment time must be in the future")
        return normalized


class AssistantReceiptCreateIn(BaseModel):
    amount_paise: int = Field(gt=0, le=100_000_000)
    method: Literal["cash", "upi", "bank_transfer", "other"]
    reference: str = Field(default="", max_length=128)
    idempotency_key: str = Field(min_length=16, max_length=128)
    paid_at: datetime | None = None

    @field_validator("paid_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Payment time must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC):
            raise ValueError("Payment time cannot be in the future")
        return normalized


async def require_assistant(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            detail={"code": "AUTH_REQUIRED", "message": "Assistant bearer token required"},
        )
    token = authorization[7:].strip()
    if len(token) < 20:
        raise HTTPException(
            401,
            detail={"code": "SESSION_INVALID", "message": "Invalid or expired assistant session"},
        )

    store = get_dental_store()
    rows = _query(
        store,
        "SELECT s.assistant_id,s.clinic_id,s.expires_at,s.revoked,a.email,a.active,c.active AS clinic_active "
        "FROM dental_assistant_sessions s "
        "JOIN dental_clinic_assistants a ON a.id=s.assistant_id AND a.clinic_id=s.clinic_id "
        "JOIN dental_clinics c ON c.id=s.clinic_id WHERE s.token_hash=%s",
        "SELECT s.assistant_id,s.clinic_id,s.expires_at,s.revoked,a.email,a.active,c.active AS clinic_active "
        "FROM dental_assistant_sessions s "
        "JOIN dental_clinic_assistants a ON a.id=s.assistant_id AND a.clinic_id=s.clinic_id "
        "JOIN dental_clinics c ON c.id=s.clinic_id WHERE s.token_hash=?",
        (_token_hash(token),),
    )
    if not rows:
        raise HTTPException(
            401,
            detail={"code": "SESSION_INVALID", "message": "Invalid or expired assistant session"},
        )
    row = rows[0]
    if (
        bool(row["revoked"])
        or not bool(row["active"])
        or not bool(row["clinic_active"])
        or int(row["expires_at"]) <= int(time.time())
    ):
        raise HTTPException(
            401,
            detail={"code": "SESSION_INVALID", "message": "Invalid or expired assistant session"},
        )
    return {
        "id": str(row["assistant_id"]),
        "user_id": str(row["assistant_id"]),
        "email": row["email"],
        "clinic_id": str(row["clinic_id"]),
        "role": "assistant",
    }


@router.post("/assistant/auth/login")
async def assistant_login(body: AssistantLoginIn, request: Request):
    _check_login_rate_limit(request)
    store = get_dental_store()
    rows = _query(
        store,
        "SELECT a.id,a.clinic_id,a.email,a.password_hash,a.active,c.active AS clinic_active "
        "FROM dental_clinic_assistants a JOIN dental_clinics c ON c.id=a.clinic_id WHERE a.email=%s",
        "SELECT a.id,a.clinic_id,a.email,a.password_hash,a.active,c.active AS clinic_active "
        "FROM dental_clinic_assistants a JOIN dental_clinics c ON c.id=a.clinic_id WHERE a.email=?",
        (body.email,),
    )
    assistant = rows[0] if rows else None
    valid = bool(
        assistant
        and bool(assistant["active"])
        and bool(assistant["clinic_active"])
        and verify_password(body.password, str(assistant["password_hash"]))
    )
    if not valid:
        raise HTTPException(
            401,
            detail={"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect"},
        )

    token = secrets.token_urlsafe(48)
    expires_at = int(time.time()) + ASSISTANT_SESSION_TTL
    with store.transaction() as connection:
        _query(
            store,
            "INSERT INTO dental_assistant_sessions(token_hash,assistant_id,clinic_id,expires_at,revoked,created_at) "
            "VALUES (%s,%s,%s,%s,false,%s)",
            "INSERT INTO dental_assistant_sessions(token_hash,assistant_id,clinic_id,expires_at,revoked,created_at) "
            "VALUES (?,?,?,?,0,?)",
            (_token_hash(token), assistant["id"], assistant["clinic_id"], expires_at, _now()),
            connection,
        )
        store.audit(
            f"assistant:{assistant['id']}",
            "session.created",
            "assistant_session",
            assistant["id"],
            assistant["clinic_id"],
            connection,
        )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ASSISTANT_SESSION_TTL,
        "user": {
            "id": assistant["id"],
            "email": assistant["email"],
            "role": "assistant",
            "clinic_id": assistant["clinic_id"],
        },
    }


@router.post("/assistant/auth/logout")
async def assistant_logout(
    actor: Annotated[dict, Depends(require_assistant)],
    authorization: str | None = Header(default=None),
):
    token = authorization[7:].strip() if authorization else ""
    store = get_dental_store()
    with store.transaction() as connection:
        _query(
            store,
            "UPDATE dental_assistant_sessions SET revoked=true WHERE token_hash=%s AND assistant_id=%s",
            "UPDATE dental_assistant_sessions SET revoked=1 WHERE token_hash=? AND assistant_id=?",
            (_token_hash(token), actor["id"]),
            connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "session.revoked",
            "assistant_session",
            actor["id"],
            actor["clinic_id"],
            connection,
        )
    return {"ok": True}


@router.get("/assistant/me")
async def assistant_me(actor: Annotated[dict, Depends(require_assistant)]):
    return {"user": actor}


@router.get("/assistant/patients")
async def assistant_list_patients(actor: Annotated[dict, Depends(require_assistant)]):
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT name,phone,created_at FROM dental_patients WHERE clinic_id=%s ORDER BY created_at DESC LIMIT 200",
            "SELECT name,phone,created_at FROM dental_patients WHERE clinic_id=? ORDER BY created_at DESC LIMIT 200",
            (actor["clinic_id"],),
            connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "read",
            "patient_directory",
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return _envelope(rows)


@router.post("/assistant/patients", status_code=201)
async def assistant_create_patient(
    body: AssistantPatientCreateIn,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    with store.transaction() as connection:
        patient = store.create_patient(
            {"name": body.name, "phone": body.phone, "age": ""},
            actor["clinic_id"],
            connection=connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "patient.intake_created",
            "patient",
            patient["id"],
            actor["clinic_id"],
            connection,
        )
    return _envelope(
        {"name": patient["name"], "phone": patient["phone"], "created_at": patient["created_at"]}
    )


@router.get("/assistant/appointments")
async def assistant_list_appointments(actor: Annotated[dict, Depends(require_assistant)]):
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT a.id,p.name AS patient_name,p.phone AS patient_phone,"
            "a.starts_at,a.status,a.created_at "
            "FROM dental_appointments a JOIN dental_patients p "
            "ON p.id=a.patient_id AND p.clinic_id=a.clinic_id "
            "WHERE a.clinic_id=%s AND a.status IN ('requested','confirmed','scheduled','rescheduled') "
            "ORDER BY a.starts_at ASC LIMIT 200",
            "SELECT a.id,p.name AS patient_name,p.phone AS patient_phone,"
            "a.starts_at,a.status,a.created_at "
            "FROM dental_appointments a JOIN dental_patients p "
            "ON p.id=a.patient_id AND p.clinic_id=a.clinic_id "
            "WHERE a.clinic_id=? AND a.status IN ('requested','confirmed','scheduled','rescheduled') "
            "ORDER BY a.starts_at ASC LIMIT 200",
            (actor["clinic_id"],),
            connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "read",
            "appointment_queue",
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return _envelope(rows)


@router.patch("/assistant/appointments/{appointment_id}")
async def assistant_update_appointment(
    appointment_id: str,
    body: AssistantAppointmentUpdateIn,
    actor: Annotated[dict, Depends(require_assistant)],
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            400,
            detail={"code": "APPOINTMENT_CHANGE_REQUIRED", "message": "Provide a status or new time"},
        )
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT id,status FROM dental_appointments WHERE id=%s AND clinic_id=%s",
            "SELECT id,status FROM dental_appointments WHERE id=? AND clinic_id=?",
            (appointment_id, actor["clinic_id"]),
            connection,
        )
        if not rows:
            raise HTTPException(404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "Appointment not found"})
        if rows[0]["status"] in {"completed", "cancelled", "no_show"}:
            raise HTTPException(
                409,
                detail={"code": "APPOINTMENT_NOT_EDITABLE", "message": "This appointment can no longer be changed"},
            )
        if body.starts_at is not None:
            changes["starts_at"] = body.starts_at.isoformat()
            if body.status is None:
                changes["status"] = "rescheduled"
        item = store.update_appointment(
            appointment_id,
            changes,
            actor["clinic_id"],
            connection=connection,
        )
        if not item:
            raise HTTPException(404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "Appointment not found"})
        store.audit(
            f"assistant:{actor['id']}",
            "appointment.updated",
            "appointment",
            appointment_id,
            actor["clinic_id"],
            connection,
        )
    return _envelope(
        {
            "id": item["id"],
            "starts_at": item["starts_at"],
            "status": item["status"],
        }
    )


@router.get("/assistant/billing/invoices")
async def assistant_list_invoice_balances(actor: Annotated[dict, Depends(require_assistant)]):
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT i.id,i.invoice_number,p.name AS patient_name,i.status,i.total_paise,i.paid_paise,"
            "(i.total_paise-i.paid_paise) AS balance_paise,i.due_date,i.issued_at,i.created_at "
            "FROM dental_invoices i JOIN dental_patients p "
            "ON p.id=i.patient_id AND p.clinic_id=i.clinic_id "
            "WHERE i.clinic_id=%s AND i.status IN ('issued','partially_paid','paid','overdue') "
            "ORDER BY i.created_at DESC LIMIT 200",
            "SELECT i.id,i.invoice_number,p.name AS patient_name,i.status,i.total_paise,i.paid_paise,"
            "(i.total_paise-i.paid_paise) AS balance_paise,i.due_date,i.issued_at,i.created_at "
            "FROM dental_invoices i JOIN dental_patients p "
            "ON p.id=i.patient_id AND p.clinic_id=i.clinic_id "
            "WHERE i.clinic_id=? AND i.status IN ('issued','partially_paid','paid','overdue') "
            "ORDER BY i.created_at DESC LIMIT 200",
            (actor["clinic_id"],),
            connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "read",
            "invoice_balances",
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    return _envelope(rows)


def _safe_receipt(receipt: dict) -> dict:
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


@router.post("/assistant/billing/invoices/{invoice_id}/receipts", status_code=201)
async def assistant_record_receipt(
    invoice_id: str,
    body: AssistantReceiptCreateIn,
    actor: Annotated[dict, Depends(require_assistant)],
):
    store = get_dental_store()
    clinic_id = actor["clinic_id"]
    with store.transaction() as connection:
        existing = _query(
            store,
            "SELECT * FROM dental_receipts WHERE clinic_id=%s AND idempotency_key=%s",
            "SELECT * FROM dental_receipts WHERE clinic_id=? AND idempotency_key=?",
            (clinic_id, body.idempotency_key),
            connection,
        )
        if existing:
            receipt = existing[0]
            if (
                receipt["invoice_id"] != invoice_id
                or int(receipt["amount_paise"]) != body.amount_paise
                or receipt["method"] != body.method
            ):
                raise HTTPException(
                    409,
                    detail={"code": "RECEIPT_IDEMPOTENCY_CONFLICT", "message": "This payment request key was already used"},
                )
            return _envelope(_safe_receipt(receipt))

        lock = " FOR UPDATE" if store.database_url else ""
        invoices = _query(
            store,
            "SELECT * FROM dental_invoices WHERE id=%s AND clinic_id=%s" + lock,
            "SELECT * FROM dental_invoices WHERE id=? AND clinic_id=?",
            (invoice_id, clinic_id),
            connection,
        )
        if not invoices:
            raise HTTPException(404, detail={"code": "INVOICE_NOT_FOUND", "message": "Invoice not found"})
        invoice = invoices[0]
        if invoice["status"] not in {"issued", "partially_paid", "overdue"}:
            raise HTTPException(
                409,
                detail={"code": "INVOICE_NOT_PAYABLE", "message": "Only an issued invoice can receive a payment"},
            )
        balance = int(invoice["total_paise"]) - int(invoice["paid_paise"])
        if body.amount_paise > balance:
            raise HTTPException(
                409,
                detail={"code": "RECEIPT_EXCEEDS_BALANCE", "message": "Payment exceeds the outstanding balance"},
            )

        now = _now()
        receipt = {
            "id": uuid.uuid4().hex,
            "clinic_id": clinic_id,
            "invoice_id": invoice_id,
            "patient_id": invoice["patient_id"],
            "receipt_number": "RCPT-" + datetime.now(UTC).strftime("%Y%m") + "-" + uuid.uuid4().hex[:8].upper(),
            "amount_paise": body.amount_paise,
            "method": body.method,
            "reference": body.reference.strip(),
            "idempotency_key": body.idempotency_key,
            "paid_at": body.paid_at.isoformat() if body.paid_at else now,
            "created_by_doctor_id": None,
            "created_by_assistant_id": actor["id"],
            "created_at": now,
        }
        inserted = _query(
            store,
            "INSERT INTO dental_receipts(id,clinic_id,invoice_id,patient_id,receipt_number,amount_paise,method,reference,idempotency_key,paid_at,created_by_doctor_id,created_by_assistant_id,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (clinic_id,idempotency_key) DO NOTHING RETURNING id",
            "INSERT INTO dental_receipts(id,clinic_id,invoice_id,patient_id,receipt_number,amount_paise,method,reference,idempotency_key,paid_at,created_by_doctor_id,created_by_assistant_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(clinic_id,idempotency_key) DO NOTHING RETURNING id",
            tuple(receipt[key] for key in (
                "id","clinic_id","invoice_id","patient_id","receipt_number","amount_paise",
                "method","reference","idempotency_key","paid_at","created_by_doctor_id",
                "created_by_assistant_id","created_at"
            )),
            connection,
        )
        if not inserted:
            duplicate = _query(
                store,
                "SELECT * FROM dental_receipts WHERE clinic_id=%s AND idempotency_key=%s",
                "SELECT * FROM dental_receipts WHERE clinic_id=? AND idempotency_key=?",
                (clinic_id, body.idempotency_key),
                connection,
            )
            if duplicate and duplicate[0]["invoice_id"] == invoice_id and int(duplicate[0]["amount_paise"]) == body.amount_paise and duplicate[0]["method"] == body.method:
                return _envelope(_safe_receipt(duplicate[0]))
            raise HTTPException(
                409,
                detail={"code": "RECEIPT_IDEMPOTENCY_CONFLICT", "message": "This payment request key was already used"},
            )

        paid = int(invoice["paid_paise"]) + body.amount_paise
        status = "paid" if paid == int(invoice["total_paise"]) else "partially_paid"
        _query(
            store,
            "UPDATE dental_invoices SET paid_paise=%s,status=%s,updated_at=%s WHERE id=%s AND clinic_id=%s",
            "UPDATE dental_invoices SET paid_paise=?,status=?,updated_at=? WHERE id=? AND clinic_id=?",
            (paid, status, now, invoice_id, clinic_id),
            connection,
        )
        store.audit(
            f"assistant:{actor['id']}",
            "invoice.receipt_recorded",
            "receipt",
            receipt["id"],
            clinic_id,
            connection,
        )
    return _envelope(_safe_receipt(receipt))


@router.post("/clinic-assistants", status_code=201)
async def create_clinic_assistant(
    body: AssistantCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            403,
            detail={"code": "CLINIC_ADMIN_REQUIRED", "message": "Only a clinic owner or administrator can manage assistants"},
        )
    store = get_dental_store()
    existing = _query(
        store,
        "SELECT id FROM dental_clinic_assistants WHERE email=%s",
        "SELECT id FROM dental_clinic_assistants WHERE email=?",
        (body.email,),
    )
    if existing:
        raise HTTPException(
            409,
            detail={"code": "ASSISTANT_EMAIL_EXISTS", "message": "An assistant account with this email already exists"},
        )
    assistant_id = uuid.uuid4().hex
    now = _now()
    with store.transaction() as connection:
        _query(
            store,
            "INSERT INTO dental_clinic_assistants(id,clinic_id,email,password_hash,active,created_by_doctor_id,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,true,%s,%s,%s)",
            "INSERT INTO dental_clinic_assistants(id,clinic_id,email,password_hash,active,created_by_doctor_id,created_at,updated_at) "
            "VALUES (?,?,?,?,1,?,?,?)",
            (
                assistant_id,
                actor["clinic_id"],
                body.email,
                hash_password(body.password),
                actor["id"],
                now,
                now,
            ),
            connection,
        )
        store.audit(
            actor["id"],
            "clinic_assistant.created",
            "clinic_assistant",
            assistant_id,
            actor["clinic_id"],
            connection,
        )
    return _envelope(
        {
            "id": assistant_id,
            "email": body.email,
            "clinic_id": actor["clinic_id"],
            "role": "assistant",
            "active": True,
            "created_at": now,
        }
    )


@router.get("/clinic-assistants")
async def list_clinic_assistants(actor: Annotated[dict, Depends(require_doctor)]):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            403,
            detail={"code": "CLINIC_ADMIN_REQUIRED", "message": "Only a clinic owner or administrator can manage assistants"},
        )
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT id,email,clinic_id,active,created_at,updated_at FROM dental_clinic_assistants WHERE clinic_id=%s ORDER BY created_at DESC",
            "SELECT id,email,clinic_id,active,created_at,updated_at FROM dental_clinic_assistants WHERE clinic_id=? ORDER BY created_at DESC",
            (actor["clinic_id"],),
            connection,
        )
        store.audit(
            actor["id"],
            "read",
            "clinic_assistant_directory",
            clinic_id=actor["clinic_id"],
            connection=connection,
        )
    for row in rows:
        row["role"] = "assistant"
    return _envelope(rows)


@router.patch("/clinic-assistants/{assistant_id}")
async def update_clinic_assistant(
    assistant_id: str,
    body: AssistantUpdateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            403,
            detail={"code": "CLINIC_ADMIN_REQUIRED", "message": "Only a clinic owner or administrator can manage assistants"},
        )
    store = get_dental_store()
    with store.transaction() as connection:
        rows = _query(
            store,
            "SELECT id,email,active FROM dental_clinic_assistants WHERE id=%s AND clinic_id=%s",
            "SELECT id,email,active FROM dental_clinic_assistants WHERE id=? AND clinic_id=?",
            (assistant_id, actor["clinic_id"]),
            connection,
        )
        if not rows:
            raise HTTPException(404, detail={"code": "ASSISTANT_NOT_FOUND", "message": "Assistant not found in this clinic"})
        now = _now()
        _query(
            store,
            "UPDATE dental_clinic_assistants SET active=%s,updated_at=%s WHERE id=%s AND clinic_id=%s",
            "UPDATE dental_clinic_assistants SET active=?,updated_at=? WHERE id=? AND clinic_id=?",
            (body.active, now, assistant_id, actor["clinic_id"]),
            connection,
        )
        if not body.active:
            _query(
                store,
                "UPDATE dental_assistant_sessions SET revoked=true WHERE assistant_id=%s",
                "UPDATE dental_assistant_sessions SET revoked=1 WHERE assistant_id=?",
                (assistant_id,),
                connection,
            )
        store.audit(
            actor["id"],
            "clinic_assistant.updated",
            "clinic_assistant",
            assistant_id,
            actor["clinic_id"],
            connection,
        )
    return _envelope(
        {
            "id": assistant_id,
            "email": rows[0]["email"],
            "clinic_id": actor["clinic_id"],
            "role": "assistant",
            "active": body.active,
            "updated_at": now,
        }
    )
