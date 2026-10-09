"""Clinic-issued email/password authentication for the Dental Doctor App.

Passwords are stored as scrypt hashes; only hashes are persisted. The one-time
bootstrap endpoint is available only while the clinic has no doctor accounts
and only when DENTAL_DOCTOR_SETUP_KEY is configured.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Header, HTTPException

from .dental_store import get_dental_store

SESSION_TTL = 60 * 60 * 24 * 30
DEFAULT_CLINIC = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _sql(store, pg: str, sqlite: str) -> str:
    return pg if store.database_url else sqlite


def _new_session(user_id: str, clinic_id: str, role: str, email: str, name: str = "") -> dict:
    session_token = secrets.token_urlsafe(48)
    identity = f"{user_id}|{clinic_id}|{role}"
    get_dental_store().create_session(identity, _hash(session_token), int(time.time()) + SESSION_TTL)
    return {
        "access_token": session_token,
        "expires_in": SESSION_TTL,
        "user": {"id": user_id, "email": email, "name": name, "role": role, "clinic_id": clinic_id},
    }


def bootstrap_doctor(email: str, password: str, display_name: str, setup_key: str) -> dict:
    """Create the first doctor account for a clinic; never enables open registration."""
    expected_key = os.getenv("DENTAL_DOCTOR_SETUP_KEY", "").strip()
    if not expected_key or not hmac.compare_digest(setup_key, expected_key):
        raise HTTPException(403, "Doctor account setup is not authorized")
    email = email.strip().lower()
    clinic_id = DEFAULT_CLINIC
    store = get_dental_store()
    existing = store._execute(
        _sql(store, "SELECT id FROM dental_doctors WHERE clinic_id=%s LIMIT 1",
             "SELECT id FROM dental_doctors WHERE clinic_id=? LIMIT 1"),
        (clinic_id,),
    )
    if existing:
        raise HTTPException(409, "A doctor account already exists for this clinic")
    user_id = secrets.token_hex(16)
    store._execute(
        _sql(store,
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at) VALUES (?,?,?,?,?,?,?)"),
        (user_id, email, "", hash_password(password), "doctor", clinic_id, str(int(time.time()))),
    )
    return _new_session(user_id, clinic_id, "doctor", email, display_name.strip())


def login_password(email: str, password: str) -> dict:
    email = email.strip().lower()
    store = get_dental_store()
    rows = store._execute(
        _sql(store,
             "SELECT id,email,password_hash,role,clinic_id FROM dental_doctors WHERE lower(email)=%s LIMIT 1",
             "SELECT id,email,password_hash,role,clinic_id FROM dental_doctors WHERE lower(email)=? LIMIT 1"),
        (email,),
    )
    if not rows or not verify_password(password, str(rows[0].get("password_hash") or "")):
        raise HTTPException(401, "Email or password is incorrect")
    doctor = rows[0]
    role = str(doctor.get("role") or "doctor")
    if role not in {"doctor", "dentist", "admin"}:
        raise HTTPException(403, "Doctor role required")
    clinic_id = str(doctor.get("clinic_id") or "")
    if not clinic_id:
        raise HTTPException(403, "Clinic access is not configured for this account")
    return _new_session(str(doctor["id"]), clinic_id, role, str(doctor["email"]))


async def logout_doctor(authorization: str | None) -> None:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            get_dental_store().revoke_session(_hash(token))


async def require_doctor(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer access token required")
    token = authorization[7:].strip()
    if not token or len(token) < 20:
        raise HTTPException(401, "Invalid or expired doctor session")
    row = get_dental_store().get_session(_hash(token))
    if not row or int(row["expires_at"]) < int(time.time()) or row.get("revoked"):
        raise HTTPException(401, "Invalid or expired doctor session")
    identity = str(row["doctor_id"])
    parts = identity.split("|", 2)
    if len(parts) != 3:
        raise HTTPException(401, "Doctor session has no clinic context")
    user_id, clinic_id, role = parts
    if role not in {"doctor", "dentist", "admin"}:
        raise HTTPException(403, "Doctor role required")
    if not clinic_id:
        raise HTTPException(403, "Clinic context required")
    return {"id": user_id, "clinic_id": clinic_id, "role": role}
