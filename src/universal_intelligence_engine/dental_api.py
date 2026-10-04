"""Dental application API: auth boundary, persistence gateway, and UIE adapter.

This module is intentionally provider-agnostic. Production deployment supplies
SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY and UIE_API_URL/UIE_API_KEY as secrets.
The mobile client never receives service-role or UIE secrets.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/dental", tags=["dental"])


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=40)
    age: str = Field(default="", max_length=10)


class AppointmentIn(BaseModel):
    patient_id: str
    starts_at: str
    treatment_type: str = Field(min_length=1, max_length=120)
    status: str = "scheduled"


class ChartEntryIn(BaseModel):
    patient_id: str
    tooth_fdi: str = Field(pattern=r"^(1[1-8]|2[1-8]|3[1-8]|4[1-8])$")
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=4000)


class IntelligenceIn(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    patient_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None
    locale: str | None = None


def require_dental_token(authorization: str | None = Header(default=None)) -> str:
    expected = os.getenv("DENTAL_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="DENTAL_API_KEY is not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid dental API credentials")
    return expected


def _supabase_headers() -> dict[str, str]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise HTTPException(status_code=503, detail="Supabase backend is not configured")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _supabase_url(table: str) -> str:
    base = os.getenv("SUPABASE_URL")
    if not base:
        raise HTTPException(status_code=503, detail="SUPABASE_URL is not configured")
    return f"{base.rstrip('/')}/rest/v1/{table}"


async def _insert(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(_supabase_url(table), headers={**_supabase_headers(), "Prefer": "return=representation"}, json=payload)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Database write failed: {response.text[:500]}")
    rows = response.json()
    return rows[0] if rows else payload


async def _select(table: str, query: str = "select=*") -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_supabase_url(table)}?{query}", headers=_supabase_headers())
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Database read failed: {response.text[:500]}")
    return response.json()


@router.get("/health")
async def dental_health(_: str = Depends(require_dental_token)) -> dict[str, Any]:
    return {"status": "ok", "service": "dental-api", "database": "supabase"}


@router.get("/patients")
async def list_patients(_: str = Depends(require_dental_token)) -> list[dict[str, Any]]:
    return await _select("dental_patients", "select=*&order=created_at.desc")


@router.post("/patients")
async def create_patient(body: PatientIn, _: str = Depends(require_dental_token)) -> dict[str, Any]:
    return await _insert("dental_patients", {"name": body.name, "phone": body.phone, "age": body.age})


@router.get("/appointments")
async def list_appointments(_: str = Depends(require_dental_token)) -> list[dict[str, Any]]:
    return await _select("dental_appointments", "select=*&order=starts_at.asc")


@router.post("/appointments")
async def create_appointment(body: AppointmentIn, _: str = Depends(require_dental_token)) -> dict[str, Any]:
    return await _insert("dental_appointments", body.model_dump())


@router.get("/patients/{patient_id}/chart")
async def get_chart(patient_id: str, _: str = Depends(require_dental_token)) -> list[dict[str, Any]]:
    return await _select("dental_chart_entries", f"select=*&patient_id=eq.{patient_id}&order=tooth_fdi.asc")


@router.post("/patients/{patient_id}/chart")
async def save_chart(patient_id: str, body: ChartEntryIn, _: str = Depends(require_dental_token)) -> dict[str, Any]:
    if body.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id mismatch")
    return await _insert("dental_chart_entries", body.model_dump())


@router.post("/intelligence/run")
async def run_dental_intelligence(body: IntelligenceIn, _: str = Depends(require_dental_token)) -> dict[str, Any]:
    # Least-privilege boundary: only dental domains are allowed across the UIE boundary.
    safe_context = {
        key: value for key, value in body.context.items()
        if key in {"patient_context", "appointments", "dental_chart", "screening", "care_pathway", "referral", "practice"}
    }
    safe_context["domain"] = "dental"
    safe_context["patient_id"] = body.patient_id
    payload = {
        "goal": body.goal,
        "language": body.language,
        "locale": body.locale,
        "context": safe_context,
        "constraints": {"require_sources": True, "human_approval_required": True},
    }
    uie_url = os.getenv("UIE_API_URL")
    uie_key = os.getenv("UIE_API_KEY")
    if not uie_url or not uie_key:
        raise HTTPException(status_code=503, detail="UIE backend is not configured")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{uie_url.rstrip('/')}/v1/intelligence/run",
            headers={"Authorization": f"Bearer {uie_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"UIE request failed: {response.text[:500]}")
    return response.json()
