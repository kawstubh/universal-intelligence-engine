"""Supabase Auth verification and Dental role authorization."""
from __future__ import annotations
import os
import httpx
from fastapi import Header, HTTPException

async def require_doctor(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer access token required")
    token = authorization[7:]
    # Controlled demo account for the current Doctor App pilot. Credentials/token
    # are supplied only through deployment environment variables, never in source.
    demo_token = os.getenv("DENTAL_DEMO_TOKEN")
    if demo_token and token == demo_token:
        return {"id": "demo-doctor", "email": os.getenv("DENTAL_DEMO_EMAIL", "doctor@drpranali.dental"), "role": "doctor", "demo": True}
    base = os.getenv("SUPABASE_URL")
    publishable = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not base or not publishable:
        # Development fallback only. Never use this in production.
        expected = os.getenv("DENTAL_API_KEY")
        if expected and token == expected:
            return {"id": "development", "role": "doctor", "development": True}
        raise HTTPException(status_code=503, detail="Supabase Auth is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{base.rstrip('/')}/auth/v1/user",
            headers={"apikey": publishable, "Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    user = response.json()
    metadata = user.get("user_metadata") or {}
    role = metadata.get("role") or (user.get("app_metadata") or {}).get("role")
    if role not in {"doctor", "admin"}:
        raise HTTPException(status_code=403, detail="Doctor/admin role required")
    return {"id": user.get("id"), "email": user.get("email"), "role": role}
