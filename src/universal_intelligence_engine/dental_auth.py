"""Clinic-issued doctor authentication with bcrypt passwords and hashed sessions."""
from __future__ import annotations

import bcrypt
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request

from .dental_store import get_dental_store

SESSION_TTL = 60 * 60 * 24 * 7
WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
DEFAULT_CLINIC = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() or "dr-pranali"
# Same bcrypt work for unknown emails and wrong passwords, avoiding account-enumeration timing.
_DUMMY_HASH = bcrypt.hashpw(hashlib.sha256(b"not-a-real-clinic-password").digest(), bcrypt.gensalt(rounds=12))
_DUMMY_SCRYPT_SALT = bytes.fromhex("00" * 16)
_DUMMY_SCRYPT_DIGEST = hashlib.scrypt(
    b"not-a-real-clinic-password", salt=_DUMMY_SCRYPT_SALT, n=2**14, r=8, p=1, dklen=32,
)


def _now() -> int:
    return int(time.time())


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    return bcrypt.hashpw(hashlib.sha256(password.encode("utf-8")).digest(), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    """Verify bcrypt or legacy scrypt while performing both KDF costs per attempt."""
    password_bytes = password.encode("utf-8")
    bcrypt_ok = False
    scrypt_ok = False
    if encoded.startswith("$2"):
        try:
            bcrypt_ok = bcrypt.checkpw(hashlib.sha256(password_bytes).digest(), encoded.encode("ascii"))
        except (ValueError, TypeError, UnicodeError):
            bcrypt.checkpw(hashlib.sha256(password_bytes).digest(), _DUMMY_HASH)
        candidate = hashlib.scrypt(password_bytes, salt=_DUMMY_SCRYPT_SALT, n=2**14, r=8, p=1, dklen=32)
        hmac.compare_digest(candidate, _DUMMY_SCRYPT_DIGEST)
    elif encoded.startswith("scrypt$"):
        try:
            scheme, salt_hex, digest_hex = encoded.split("$", 2)
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.scrypt(password_bytes, salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=len(expected))
            scrypt_ok = scheme == "scrypt" and hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            candidate = hashlib.scrypt(password_bytes, salt=_DUMMY_SCRYPT_SALT, n=2**14, r=8, p=1, dklen=32)
            hmac.compare_digest(candidate, _DUMMY_SCRYPT_DIGEST)
        bcrypt.checkpw(hashlib.sha256(password_bytes).digest(), _DUMMY_HASH)
    else:
        bcrypt.checkpw(hashlib.sha256(password_bytes).digest(), _DUMMY_HASH)
        candidate = hashlib.scrypt(password_bytes, salt=_DUMMY_SCRYPT_SALT, n=2**14, r=8, p=1, dklen=32)
        hmac.compare_digest(candidate, _DUMMY_SCRYPT_DIGEST)
    return bcrypt_ok or scrypt_ok


def _sql(store, pg: str, sqlite: str) -> str:
    return pg if store.database_url else sqlite


def _audit_login(email: str, success: bool, ip: str, code: str) -> None:
    store = get_dental_store()
    store._execute(
        _sql(store,
             "INSERT INTO dental_login_audit(id,occurred_at,email,success,ip_address,code) VALUES (%s,%s,%s,%s,%s,%s)",
             "INSERT INTO dental_login_audit(id,occurred_at,email,success,ip_address,code) VALUES (?,?,?,?,?,?)"),
        (secrets.token_hex(16), _iso_now(), email[:254], success, ip[:64], code[:64]),
    )


def _throttle_keys(email: str, ip: str) -> list[tuple[str, str]]:
    return [("email", _hash(email)), ("ip", _hash(ip or "unknown"))]


def _lock_state(email: str, ip: str) -> bool:
    store = get_dental_store()
    now = _now()
    for kind, key in _throttle_keys(email, ip):
        rows = store._execute(
            _sql(store,
                 "SELECT locked_until FROM dental_auth_throttle WHERE scope_type=%s AND scope_key=%s",
                 "SELECT locked_until FROM dental_auth_throttle WHERE scope_type=? AND scope_key=?"),
            (kind, key),
        )
        if rows and int(rows[0].get("locked_until") or 0) > now:
            return True
    return False


def _record_failure(email: str, ip: str) -> None:
    store = get_dental_store()
    now = _now()
    for kind, key in _throttle_keys(email, ip):
        rows = store._execute(
            _sql(store,
                 "SELECT failure_count,window_started_at FROM dental_auth_throttle WHERE scope_type=%s AND scope_key=%s",
                 "SELECT failure_count,window_started_at FROM dental_auth_throttle WHERE scope_type=? AND scope_key=?"),
            (kind, key),
        )
        if not rows or now - int(rows[0]["window_started_at"]) >= WINDOW_SECONDS:
            count, start = 1, now
        else:
            count, start = int(rows[0]["failure_count"]) + 1, int(rows[0]["window_started_at"])
        locked_until = now + LOCK_SECONDS if count >= MAX_FAILURES else 0
        if rows:
            store._execute(
                _sql(store,
                     "UPDATE dental_auth_throttle SET failure_count=%s,window_started_at=%s,locked_until=%s,updated_at=%s WHERE scope_type=%s AND scope_key=%s",
                     "UPDATE dental_auth_throttle SET failure_count=?,window_started_at=?,locked_until=?,updated_at=? WHERE scope_type=? AND scope_key=?"),
                (count, start, locked_until, now, kind, key),
            )
        else:
            store._execute(
                _sql(store,
                     "INSERT INTO dental_auth_throttle(scope_type,scope_key,failure_count,window_started_at,locked_until,updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                     "INSERT INTO dental_auth_throttle(scope_type,scope_key,failure_count,window_started_at,locked_until,updated_at) VALUES (?,?,?,?,?,?)"),
                (kind, key, count, start, locked_until, now),
            )


def _clear_throttle(email: str, ip: str) -> None:
    store = get_dental_store()
    for kind, key in _throttle_keys(email, ip):
        store._execute(
            _sql(store,
                 "DELETE FROM dental_auth_throttle WHERE scope_type=%s AND scope_key=%s",
                 "DELETE FROM dental_auth_throttle WHERE scope_type=? AND scope_key=?"),
            (kind, key),
        )


def _create_session(doctor: dict) -> dict:
    token = secrets.token_urlsafe(48)
    identity = f"{doctor['id']}|{doctor['clinic_id']}|{doctor['role']}"
    store = get_dental_store()
    store.create_session(identity, _hash(token), _now() + SESSION_TTL)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": SESSION_TTL,
        "must_change_password": bool(doctor.get("must_change_password")),
        "user": {
            "id": str(doctor["id"]), "email": str(doctor["email"]),
            "name": str(doctor.get("display_name") or ""),
            "role": str(doctor["role"]), "clinic_id": str(doctor["clinic_id"]),
            "must_change_password": bool(doctor.get("must_change_password")),
        },
    }


def _auth_error(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def bootstrap_doctor(email: str, password: str, display_name: str, setup_token: str) -> dict:
    """One-time first-admin setup; no token or open admin registration after first admin."""
    expected = os.getenv("DENTAL_SETUP_TOKEN", "")
    if not expected or not hmac.compare_digest(setup_token or "", expected):
        _auth_error(403, "SETUP_NOT_AUTHORIZED", "Initial clinic setup is not authorized.")
    if len(password) < 10:
        _auth_error(422, "PASSWORD_POLICY", "Password must be at least 10 characters.")
    email = email.strip().lower()
    store = get_dental_store()
    admins = store._execute(
        "SELECT id FROM dental_doctors WHERE role='admin' LIMIT 1", (),
    )
    if admins:
        _auth_error(409, "SETUP_ALREADY_COMPLETED", "Initial setup has already been completed.")
    existing = store.doctor_by_email(email)
    if existing:
        _auth_error(409, "EMAIL_ALREADY_EXISTS", "An account already exists for this email.")
    user_id = secrets.token_hex(16)
    store._execute(
        _sql(store,
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (?,?,?,?,?,?,?,?,?,?)"),
        (user_id, email, "", hash_password(password), "admin", DEFAULT_CLINIC, str(_now()), True, False, display_name.strip()),
    )
    return _create_session({"id": user_id, "email": email, "display_name": display_name.strip(),
                            "role": "admin", "clinic_id": DEFAULT_CLINIC, "must_change_password": False})


def login_password(email: str, password: str, ip: str = "unknown") -> dict:
    email = email.strip().lower()
    ip = ip or "unknown"
    if _lock_state(email, ip):
        _audit_login(email, False, ip, "ACCOUNT_LOCKED")
        _auth_error(423, "ACCOUNT_LOCKED", "Sign-in is temporarily locked. Try again later.")
    store = get_dental_store()
    doctor = store.doctor_by_email(email)
    encoded = str((doctor or {}).get("password_hash") or _DUMMY_HASH.decode("ascii"))
    password_ok = verify_password(password, encoded)
    valid = bool(doctor and password_ok and doctor.get("is_active", True))
    if not valid:
        _record_failure(email, ip)
        _audit_login(email, False, ip, "INVALID_CREDENTIALS")
        _auth_error(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    # Upgrade a verified legacy scrypt hash to bcrypt on successful login.
    if str(doctor.get("password_hash") or "").startswith("scrypt$"):
        upgraded_hash = hash_password(password)
        store._execute(
            _sql(store,
                 "UPDATE dental_doctors SET password_hash=%s,password_changed_at=%s WHERE id=%s AND clinic_id=%s",
                 "UPDATE dental_doctors SET password_hash=?,password_changed_at=? WHERE id=? AND clinic_id=?"),
            (upgraded_hash, str(_now()), doctor["id"], doctor["clinic_id"]),
        )
        doctor["password_hash"] = upgraded_hash
    _clear_throttle(email, ip)
    _audit_login(email, True, ip, "OK")
    return _create_session(doctor)


def _revoke_doctor_sessions(doctor_id: str) -> None:
    store = get_dental_store()
    store._execute(
        _sql(store,
             "UPDATE dental_sessions SET revoked=true WHERE doctor_id LIKE %s",
             "UPDATE dental_sessions SET revoked=1 WHERE doctor_id LIKE ?"),
        (doctor_id + "|%",),
    )


async def logout_doctor(authorization: str | None) -> None:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            get_dental_store().revoke_session(_hash(token))


async def require_doctor(request: Request, authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        _auth_error(401, "SESSION_EXPIRED", "Sign in again to continue.")
    token = authorization[7:].strip()
    if len(token) < 40:
        _auth_error(401, "SESSION_EXPIRED", "Sign in again to continue.")
    row = get_dental_store().get_session(_hash(token))
    if not row:
        _auth_error(401, "SESSION_EXPIRED", "Sign in again to continue.")
    if row.get("revoked"):
        _auth_error(401, "SESSION_REVOKED", "This session was revoked. Sign in again.")
    if int(row["expires_at"]) <= _now():
        _auth_error(401, "SESSION_EXPIRED", "Your session expired. Sign in again.")
    parts = str(row["doctor_id"]).split("|", 2)
    if len(parts) != 3:
        _auth_error(401, "SESSION_REVOKED", "This session is invalid. Sign in again.")
    user_id, clinic_id, role = parts
    store = get_dental_store()
    rows = store._execute(
        _sql(store,
             "SELECT is_active,must_change_password FROM dental_doctors WHERE id=%s AND clinic_id=%s",
             "SELECT is_active,must_change_password FROM dental_doctors WHERE id=? AND clinic_id=?"),
        (user_id, clinic_id),
    )
    if not rows or not rows[0].get("is_active", True):
        _revoke_doctor_sessions(user_id)
        _auth_error(401, "SESSION_REVOKED", "This account is inactive. Contact the clinic administrator.")
    must_change = bool(rows[0].get("must_change_password"))
    if must_change and request.url.path != "/v1/dental/auth/password/change":
        _auth_error(403, "PASSWORD_CHANGE_REQUIRED", "Change your temporary password before continuing.")
    if role not in {"doctor", "dentist", "admin"} or not clinic_id:
        _auth_error(403, "CLINIC_ACCESS_DENIED", "Clinic access is not configured.")
    return {"id": user_id, "clinic_id": clinic_id, "role": role, "must_change_password": must_change}


def change_password(doctor: dict, current_password: str, new_password: str) -> dict:
    if len(new_password) < 10:
        _auth_error(422, "PASSWORD_POLICY", "Password must be at least 10 characters.")
    store = get_dental_store()
    rows = store._execute(
        _sql(store,
             "SELECT password_hash FROM dental_doctors WHERE id=%s AND clinic_id=%s AND is_active=true",
             "SELECT password_hash FROM dental_doctors WHERE id=? AND clinic_id=? AND is_active=1"),
        (doctor["id"], doctor["clinic_id"]),
    )
    if not rows or not verify_password(current_password, str(rows[0]["password_hash"])):
        _auth_error(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    store._execute(
        _sql(store,
             "UPDATE dental_doctors SET password_hash=%s,must_change_password=false,password_changed_at=%s WHERE id=%s AND clinic_id=%s",
             "UPDATE dental_doctors SET password_hash=?,must_change_password=0,password_changed_at=? WHERE id=? AND clinic_id=?"),
        (hash_password(new_password), str(_now()), doctor["id"], doctor["clinic_id"]),
    )
    _revoke_doctor_sessions(doctor["id"])
    return {"ok": True, "code": "PASSWORD_CHANGED", "sessions_revoked": True}


def _require_admin(actor: dict) -> None:
    if actor.get("role") != "admin":
        _auth_error(403, "ADMIN_REQUIRED", "Clinic administrator permission is required.")


def add_doctor(actor: dict, email: str, display_name: str) -> dict:
    _require_admin(actor)
    email = email.strip().lower()
    temporary_password = secrets.token_urlsafe(18)
    store = get_dental_store()
    existing = store.doctor_by_email(email)
    if existing:
        _auth_error(409, "EMAIL_ALREADY_EXISTS", "An account already exists for this email.")
    user_id = secrets.token_hex(16)
    store._execute(
        _sql(store,
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
             "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (?,?,?,?,?,?,?,?,?,?)"),
        (user_id, email, "", hash_password(temporary_password), "doctor", actor["clinic_id"], str(_now()), True, True, display_name.strip()),
    )
    return {"id": user_id, "email": email, "temporary_password": temporary_password, "must_change_password": True}


def deactivate_doctor(actor: dict, doctor_id: str) -> dict:
    _require_admin(actor)
    store = get_dental_store()
    target = store._execute(
        _sql(store,
             "SELECT id,role FROM dental_doctors WHERE id=%s AND clinic_id=%s",
             "SELECT id,role FROM dental_doctors WHERE id=? AND clinic_id=?"),
        (doctor_id, actor["clinic_id"]),
    )
    if not target:
        _auth_error(404, "DOCTOR_NOT_FOUND", "Doctor not found in this clinic.")
    if target[0]["role"] == "admin":
        _auth_error(409, "ADMIN_CANNOT_BE_DEACTIVATED", "The clinic administrator cannot be deactivated through doctor management.")
    store._execute(
        _sql(store,
             "UPDATE dental_doctors SET is_active=false WHERE id=%s AND clinic_id=%s AND role<>'admin'",
             "UPDATE dental_doctors SET is_active=0 WHERE id=? AND clinic_id=? AND role<>'admin'"),
        (doctor_id, actor["clinic_id"]),
    )
    _revoke_doctor_sessions(doctor_id)
    return {"ok": True, "doctor_id": doctor_id, "active": False, "sessions_revoked": True}


def reset_doctor_password(actor: dict, doctor_id: str) -> dict:
    _require_admin(actor)
    temporary_password = secrets.token_urlsafe(18)
    store = get_dental_store()
    rows = store._execute(
        _sql(store,
             "UPDATE dental_doctors SET password_hash=%s,must_change_password=true,password_changed_at=%s WHERE id=%s AND clinic_id=%s AND role<>'admin' RETURNING id,email",
             "UPDATE dental_doctors SET password_hash=?,must_change_password=1,password_changed_at=? WHERE id=? AND clinic_id=? AND role<>'admin'"),
        (hash_password(temporary_password), str(_now()), doctor_id, actor["clinic_id"]),
    )
    if not rows:
        exists = store._execute(
            _sql(store, "SELECT id,email FROM dental_doctors WHERE id=%s AND clinic_id=%s AND role<>'admin'",
                 "SELECT id,email FROM dental_doctors WHERE id=? AND clinic_id=? AND role<>'admin'"),
            (doctor_id, actor["clinic_id"]),
        )
        if not exists:
            _auth_error(404, "DOCTOR_NOT_FOUND", "Doctor not found in this clinic.")
        rows = exists
    _revoke_doctor_sessions(doctor_id)
    return {"ok": True, "doctor_id": doctor_id, "email": rows[0]["email"],
            "temporary_password": temporary_password, "must_change_password": True, "sessions_revoked": True}
