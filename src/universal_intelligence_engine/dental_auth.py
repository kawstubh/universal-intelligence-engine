"""Password-based authentication for the Dental Doctor App.

Google/Supabase are intentionally not part of this authentication path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from .dental_store import get_dental_store

SESSION_TTL = 60 * 60 * 24 * 30
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "scrypt$" + str(_SCRYPT_N) + "$" + str(_SCRYPT_R) + "$" + str(_SCRYPT_P) + "$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def _bootstrap_admin() -> None:
    email = os.getenv("DENTAL_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return
    if len(password) < 12:
        raise RuntimeError("DENTAL_BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
    store = get_dental_store()
    if store.doctor_by_email(email):
        return
    clinic_id = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"
    store.create_doctor({"id": secrets.token_hex(16), "email": email, "password_hash": hash_password(password), "role": "admin", "clinic_id": clinic_id})


def _check_login_rate_limit(ip: str) -> None:
    now = time.monotonic()
    attempts = _LOGIN_ATTEMPTS[ip]
    while attempts and now - attempts[0] > 300:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, detail={"code": "LOGIN_RATE_LIMITED", "message": "Too many login attempts; try again in five minutes"})
    attempts.append(now)


async def login_password(email: str, password: str, request: Request) -> dict:
    _check_login_rate_limit(request.client.host if request.client else "unknown")
    _bootstrap_admin()
    normalized_email = email.strip().lower()
    doctor = get_dental_store().doctor_by_email(normalized_email)
    if not doctor or not doctor.get("active", True) or not verify_password(password, str(doctor.get("password_hash") or "")):
        raise HTTPException(401, detail={"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect"})
    token = secrets.token_urlsafe(48)
    expires_at = int(time.time()) + SESSION_TTL
    get_dental_store().create_session(f"{doctor['id']}|{doctor['clinic_id']}|{doctor['role']}", _hash(token), expires_at)
    return {"access_token": token, "token_type": "Bearer", "expires_in": SESSION_TTL, "user": {"id": doctor["id"], "email": doctor["email"], "role": doctor["role"], "clinic_id": doctor["clinic_id"]}}


async def logout_doctor(authorization: str | None) -> None:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            get_dental_store().revoke_session(_hash(token))


async def require_doctor(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "Bearer access token required"})
    token = authorization[7:].strip()
    if not token or len(token) < 20:
        raise HTTPException(401, detail={"code": "SESSION_INVALID", "message": "Invalid or expired doctor session"})
    row = get_dental_store().get_session(_hash(token))
    if not row or int(float(row["expires_at"])) < int(time.time()) or row.get("revoked"):
        raise HTTPException(401, detail={"code": "SESSION_INVALID", "message": "Invalid or expired doctor session"})
    parts = str(row["doctor_id"]).split("|", 2)
    if len(parts) != 3:
        raise HTTPException(401, detail={"code": "SESSION_INVALID", "message": "Doctor session has no clinic context"})
    user_id, clinic_id, role = parts
    doctor = get_dental_store().doctor_by_id(user_id)
    if not doctor or not doctor.get("active", True):
        raise HTTPException(401, detail={"code": "ACCOUNT_DISABLED", "message": "This clinic account is inactive"})
    if str(doctor.get("clinic_id")) != clinic_id or str(doctor.get("role")) != role:
        raise HTTPException(401, detail={"code": "SESSION_INVALID", "message": "Session account permissions changed"})
    if role not in {"doctor", "dentist", "admin"}:
        raise HTTPException(403, detail={"code": "DOCTOR_ROLE_REQUIRED", "message": "Doctor role required"})
    if not clinic_id:
        raise HTTPException(403, detail={"code": "CLINIC_CONTEXT_REQUIRED", "message": "Clinic context required"})
    return {"id": user_id, "user_id": user_id, "email": doctor["email"], "clinic_id": clinic_id, "role": role}
