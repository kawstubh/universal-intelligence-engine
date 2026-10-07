"""Production Google-only authentication for the Dental Doctor App."""
from __future__ import annotations

import hashlib
import os
import secrets
import time

import httpx
from fastapi import Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from .dental_store import get_dental_store

SESSION_TTL = 60 * 60 * 24 * 30
GOOGLE_WEB_CLIENT_ID = os.getenv("DENTAL_GOOGLE_WEB_CLIENT_ID", "").strip()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _verify_google_id_token(google_token: str) -> dict:
    if not GOOGLE_WEB_CLIENT_ID:
        raise HTTPException(
            503,
            detail={
                "message": "Google authentication is not configured on the Dental API",
                "code": "GOOGLE_WEB_CLIENT_ID_MISSING",
            },
        )
    try:
        return google_id_token.verify_oauth2_token(
            google_token,
            google_requests.Request(),
            GOOGLE_WEB_CLIENT_ID,
        )
    except ValueError as exc:
        raise HTTPException(
            401,
            detail={
                "message": "Google ID token rejected",
                "code": "GOOGLE_ID_TOKEN_INVALID",
            },
        ) from exc
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise HTTPException(
            502,
            detail={
                "message": "Google token verification could not be completed",
                "code": "GOOGLE_VERIFICATION_ERROR",
                "google_status": status,
            },
        ) from exc


async def login_google(access_token: str, google_token: str) -> dict:
    """Verify Google identity, then require an active clinic membership."""
    google_claims = _verify_google_id_token((google_token or "").strip())
    token = (access_token or "").strip()
    if not token:
        raise HTTPException(400, "Google access token is required")

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    publishable_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not supabase_url or not publishable_key:
        raise HTTPException(
            503,
            detail={
                "message": "Google authentication is not configured on the Dental API",
                "code": "SUPABASE_CONFIG_MISSING",
            },
        )

    headers = {"apikey": publishable_key, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        user_response = await client.get(f"{supabase_url}/auth/v1/user", headers=headers)
        if user_response.status_code != 200:
            raise HTTPException(
                401,
                detail={
                    "message": "Supabase rejected the authenticated Google session",
                    "code": "SUPABASE_SESSION_INVALID",
                },
            )
        profile = user_response.json()
        google_email = str(google_claims.get("email") or "").strip().lower()
        profile_email = str(profile.get("email") or "").strip().lower()
        if google_email and profile_email and google_email != profile_email:
            raise HTTPException(
                401,
                detail={
                    "message": "Google identity does not match the Supabase identity",
                    "code": "GOOGLE_SUPABASE_IDENTITY_MISMATCH",
                },
            )
        user_id = str(profile.get("id") or "").strip()
        if not user_id:
            raise HTTPException(401, "Supabase user id is missing")

        membership_response = await client.get(
            f"{supabase_url}/rest/v1/dental_clinic_memberships",
            params={
                "user_id": f"eq.{user_id}",
                "active": "eq.true",
                "select": "clinic_id,role,active,dental_clinics(id,name,slug,logo_url,phone,email,address,active)",
                "order": "created_at.asc",
            },
            headers=headers,
        )

    if membership_response.status_code != 200:
        raise HTTPException(
            503,
            detail={
                "message": "Unable to resolve clinic membership",
                "code": "CLINIC_MEMBERSHIP_LOOKUP_FAILED",
            },
        )

    memberships = [
        m for m in membership_response.json()
        if m.get("dental_clinics", {}).get("active", True)
    ]
    if not memberships:
        raise HTTPException(403, "Your Google account is not yet authorized for a dental clinic")

    clinics = [
        {"clinic_id": m["clinic_id"], "role": m["role"], "clinic": m.get("dental_clinics") or {}}
        for m in memberships
    ]
    selected = clinics[0]
    session_token = secrets.token_urlsafe(48)
    identity = f"{user_id}|{selected['clinic_id']}|{selected['role']}"
    get_dental_store().create_session(
        identity,
        _hash(session_token),
        int(time.time()) + SESSION_TTL,
    )
    metadata = profile.get("user_metadata") or {}
    return {
        "access_token": session_token,
        "expires_in": SESSION_TTL,
        "user": {
            "id": user_id,
            "email": str(profile.get("email") or ""),
            "name": metadata.get("full_name") or metadata.get("name") or "",
            "role": selected["role"],
            "clinic_id": selected["clinic_id"],
        },
        "clinics": clinics,
    }


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
