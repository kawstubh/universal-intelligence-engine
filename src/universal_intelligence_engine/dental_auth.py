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
import uuid
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
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return (
        "scrypt$"
        + str(_SCRYPT_N)
        + "$"
        + str(_SCRYPT_R)
        + "$"
        + str(_SCRYPT_P)
        + "$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(derived).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def _bootstrap_admin() -> None:
    email = os.getenv("DENTAL_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return
    if len(password) < 12:
        raise RuntimeError(
            "DENTAL_BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters"
        )
    store = get_dental_store()
    if store.doctor_by_email(email):
        return
    configured_clinic_id = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()
    if configured_clinic_id:
        clinic_id = configured_clinic_id
    else:
        existing_clinics = store._execute(
            "SELECT id FROM dental_clinics ORDER BY id"
        )
        existing_clinic_ids = [str(row["id"]) for row in existing_clinics]
        if "dr-pranali" in existing_clinic_ids:
            clinic_id = "dr-pranali"
        elif len(existing_clinic_ids) == 1:
            clinic_id = existing_clinic_ids[0]
        elif len(existing_clinic_ids) > 1:
            raise RuntimeError(
                "Multiple clinic workspaces exist. Set DENTAL_MASTER_CLINIC_ID "
                "before creating the bootstrap administrator."
            )
        else:
            clinic_id = "dr-pranali"
    clinic_rows = store._execute(
        "SELECT id FROM dental_clinics WHERE id=%s"
        if store.database_url
        else "SELECT id FROM dental_clinics WHERE id=?",
        (clinic_id,),
    )
    if not clinic_rows:
        organisation_id = "org-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"uie:dental:legacy-organisation:{clinic_id}",
        ).hex
        organisation_rows = store._execute(
            "SELECT id FROM dental_organisations WHERE id=%s"
            if store.database_url
            else "SELECT id FROM dental_organisations WHERE id=?",
            (organisation_id,),
        )
        if not organisation_rows:
            store.create_organisation(
                {
                    "id": organisation_id,
                    "name": os.getenv(
                        "DENTAL_MASTER_CLINIC_NAME", "Dr Pranali Dental Clinic"
                    ).strip(),
                    "organisation_type": "clinic",
                }
            )
        store.create_clinic(
            {
                "id": clinic_id,
                "organisation_id": organisation_id,
                "name": os.getenv(
                    "DENTAL_MASTER_CLINIC_NAME", "Dr Pranali Dental Clinic"
                ).strip(),
                "clinic_type": "clinic",
            }
        )

    doctor_id = secrets.token_hex(16)
    store.create_doctor(
        {
            "id": doctor_id,
            "email": email,
            "password_hash": hash_password(password),
            "role": "admin",
            "clinic_id": clinic_id,
        }
    )
    if not store.doctor_membership(doctor_id, clinic_id):
        store.create_doctor_membership(doctor_id, clinic_id, "owner")


def _check_login_rate_limit(ip: str) -> None:
    now = time.monotonic()
    attempts = _LOGIN_ATTEMPTS[ip]
    while attempts and now - attempts[0] > 300:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(
            429,
            detail={
                "code": "LOGIN_RATE_LIMITED",
                "message": "Too many login attempts; try again in five minutes",
            },
        )
    attempts.append(now)


async def login_password(email: str, password: str, request: Request) -> dict:
    _check_login_rate_limit(request.client.host if request.client else "unknown")
    _bootstrap_admin()
    normalized_email = email.strip().lower()
    store = get_dental_store()
    doctor = store.doctor_by_email(normalized_email)
    if (
        not doctor
        or not doctor.get("active", True)
        or not verify_password(password, str(doctor.get("password_hash") or ""))
    ):
        raise HTTPException(
            401,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Email or password is incorrect",
            },
        )

    workspaces = store.workspaces_for_doctor(str(doctor["id"]))
    if not workspaces:
        raise HTTPException(
            403,
            detail={
                "code": "NO_ACTIVE_WORKSPACE",
                "message": "This doctor has no active clinic workspace membership",
            },
        )
    default_workspace = next(
        (
            workspace
            for workspace in workspaces
            if str(workspace["id"]) == str(doctor.get("clinic_id") or "")
        ),
        workspaces[0],
    )
    clinic_id = str(default_workspace["id"])
    role = str(default_workspace["role"])
    token = secrets.token_urlsafe(48)
    expires_at = int(time.time()) + SESSION_TTL
    store.create_session(
        f"{doctor['id']}|{clinic_id}|{role}",
        _hash(token),
        expires_at,
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": SESSION_TTL,
        "user": {
            "id": doctor["id"],
            "email": doctor["email"],
            "role": role,
            "clinic_id": clinic_id,
        },
        "workspaces": workspaces,
    }


async def login_google(id_token: str, request: Request) -> dict:
    """Exchange a Google ID token for a clinic session; never trust client-supplied email."""
    _check_login_rate_limit(request.client.host if request.client else "unknown")
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    allowed = {
        item.strip().lower()
        for item in os.getenv("DENTAL_GOOGLE_ALLOWED_EMAILS", "").split(",")
        if item.strip()
    }
    if not client_id or not allowed:
        raise HTTPException(
            503,
            detail={"code": "GOOGLE_LOGIN_NOT_CONFIGURED", "message": "Google sign-in is not configured for this clinic"},
        )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
        if response.status_code != 200:
            raise ValueError("Google token rejected")
        claims = response.json()
        if (
            claims.get("aud") != client_id
            or claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
            or claims.get("email_verified") not in {True, "true"}
            or int(claims.get("exp", 0)) <= int(time.time())
        ):
            raise ValueError("Google token claims invalid")
        email = str(claims.get("email", "")).strip().lower()
        if not email or email not in allowed:
            raise ValueError("Google identity not authorised")
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise HTTPException(
            401,
            detail={"code": "GOOGLE_TOKEN_INVALID", "message": "Google sign-in could not be verified"},
        ) from None

    store = get_dental_store()
    linked_doctor_email = os.getenv("DENTAL_GOOGLE_DOCTOR_EMAIL", "").strip().lower() or email
    doctor = store.doctor_by_email(linked_doctor_email)
    if not doctor or not doctor.get("active", True):
        raise HTTPException(
            403,
            detail={"code": "GOOGLE_ACCOUNT_NOT_LINKED", "message": "This Google account is not linked to an active doctor account"},
        )
    workspaces = store.workspaces_for_doctor(str(doctor["id"]))
    if not workspaces:
        raise HTTPException(
            403,
            detail={"code": "NO_ACTIVE_WORKSPACE", "message": "This doctor has no active clinic workspace membership"},
        )
    default_workspace = next(
        (workspace for workspace in workspaces if str(workspace["id"]) == str(doctor.get("clinic_id") or "")),
        workspaces[0],
    )
    clinic_id = str(default_workspace["id"])
    role = str(default_workspace["role"])
    token = secrets.token_urlsafe(48)
    expires_at = int(time.time()) + SESSION_TTL
    store.create_session(f"{doctor['id']}|{clinic_id}|{role}", _hash(token), expires_at)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": SESSION_TTL,
        "user": {"id": doctor["id"], "email": doctor["email"], "role": role, "clinic_id": clinic_id},
        "workspaces": workspaces,
    }


async def logout_doctor(authorization: str | None) -> None:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            get_dental_store().revoke_session(_hash(token))


async def require_doctor(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            detail={"code": "AUTH_REQUIRED", "message": "Bearer access token required"},
        )
    token = authorization[7:].strip()
    if not token or len(token) < 20:
        raise HTTPException(
            401,
            detail={
                "code": "SESSION_INVALID",
                "message": "Invalid or expired doctor session",
            },
        )
    store = get_dental_store()
    row = store.get_session(_hash(token))
    if (
        not row
        or int(float(row["expires_at"])) < int(time.time())
        or row.get("revoked")
    ):
        raise HTTPException(
            401,
            detail={
                "code": "SESSION_INVALID",
                "message": "Invalid or expired doctor session",
            },
        )
    parts = str(row["doctor_id"]).split("|", 2)
    if len(parts) < 2:
        raise HTTPException(
            401,
            detail={
                "code": "SESSION_INVALID",
                "message": "Doctor session has no clinic context",
            },
        )
    user_id, clinic_id = parts[0], parts[1]
    doctor = store.doctor_by_id(user_id)
    if not doctor or not doctor.get("active", True):
        raise HTTPException(
            401,
            detail={
                "code": "ACCOUNT_DISABLED",
                "message": "This doctor account is inactive",
            },
        )

    workspace = next(
        (
            item
            for item in store.workspaces_for_doctor(user_id)
            if str(item["id"]) == clinic_id
        ),
        None,
    )
    if not workspace:
        raise HTTPException(
            401,
            detail={
                "code": "WORKSPACE_ACCESS_REVOKED",
                "message": "This doctor no longer has access to the active workspace",
            },
        )
    role = str(workspace["role"])
    if role not in {"owner", "admin", "doctor", "dentist"}:
        raise HTTPException(
            403,
            detail={"code": "DOCTOR_ROLE_REQUIRED", "message": "Doctor role required"},
        )
    return {
        "id": user_id,
        "user_id": user_id,
        "email": doctor["email"],
        "clinic_id": clinic_id,
        "role": role,
    }


async def switch_doctor_workspace(
    authorization: str | None,
    clinic_id: str,
    current_user: dict | None = None,
) -> dict:
    if current_user is None:
        current_user = await require_doctor(authorization)
    store = get_dental_store()
    workspaces = store.workspaces_for_doctor(current_user["id"])
    target = next(
        (workspace for workspace in workspaces if str(workspace["id"]) == clinic_id),
        None,
    )
    if not target:
        raise HTTPException(
            403,
            detail={
                "code": "WORKSPACE_ACCESS_DENIED",
                "message": "You do not have active access to this workspace",
            },
        )

    new_token = secrets.token_urlsafe(48)
    expires_at = int(time.time()) + SESSION_TTL
    store.create_session(
        f"{current_user['id']}|{clinic_id}|{target['role']}",
        _hash(new_token),
        expires_at,
    )
    old_token = authorization[7:].strip() if authorization else ""
    if old_token:
        store.revoke_session(_hash(old_token))

    return {
        "access_token": new_token,
        "token_type": "Bearer",
        "expires_in": SESSION_TTL,
        "user": {
            "id": current_user["id"],
            "email": current_user["email"],
            "role": target["role"],
            "clinic_id": clinic_id,
        },
        "workspaces": workspaces,
    }
