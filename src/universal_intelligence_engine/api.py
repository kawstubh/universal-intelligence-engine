"""HTTP API for the Universal Intelligence Engine and Dental application layer."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .contracts import IntelligenceRequest, KnowledgeProvider
from .dental_ai import router as dental_ai_router
from .dental_api import router as dental_router
from .dental_store import get_dental_store
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .memory import LearningMemory
from .providers import (
    FreePublicKnowledgeProvider,
    OpenAIResponsesReasoningProvider,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
)
from .reasoning import NativeReasoningProvider
from .service import IntelligenceService


class IntelligenceRunBody(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    context: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None
    language: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


def build_service() -> IntelligenceService:
    # Public reference and biomedical APIs require no paid search subscription or key.
    knowledge: KnowledgeProvider = FreePublicKnowledgeProvider()
    reasoning = (
        OpenAIResponsesReasoningProvider()
        if os.getenv("OPENAI_API_KEY")
        else NativeReasoningProvider()
    )
    memory = LearningMemory(os.getenv("UIE_MEMORY_PATH", ".data/learning.jsonl"))
    engine = UniversalIntelligenceEngine(
        knowledge=knowledge,
        reasoning=reasoning,
        evaluator=BasicResponseEvaluator(),
        learning_store=memory,
    )
    return IntelligenceService(engine)


app = FastAPI(
    title="Universal Intelligence Engine",
    version=__version__,
    description="Shared intelligence API for research, reasoning, applications, agents, and Dental Intelligence.",
)
service = build_service()


# Launch policy: patient-facing AI is disabled at the API boundary, including
# legacy routes that may still be registered by the Dental router.
@app.middleware("http")
async def disable_patient_ai(request: Request, call_next):
    path = request.url.path.rstrip("/")
    if path == "/v1/dental/ai/patient" or path.startswith("/v1/dental/ai/patient/"):
        return JSONResponse(
            status_code=404,
            content={
                "detail": {
                    "code": "PATIENT_AI_DISABLED",
                    "message": "Patient-facing AI is not available.",
                }
            },
        )
    return await call_next(request)


app.include_router(dental_router)
app.include_router(dental_ai_router)


def _doctor_only_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["paths"] = {
        path: operations
        for path, operations in schema.get("paths", {}).items()
        if not (
            path.rstrip("/") == "/v1/dental/ai/patient"
            or path.startswith("/v1/dental/ai/patient/")
        )
    }
    app.openapi_schema = schema
    return schema


app.__dict__["openapi"] = _doctor_only_openapi


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("UIE_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="UIE_API_KEY is not configured; API access is fail-closed.",
        )
    supplied = authorization or ""
    expected_header = f"Bearer {expected}"
    if not hmac.compare_digest(supplied, expected_header):
        raise HTTPException(status_code=401, detail="Invalid API credentials")


@app.get("/ready")
def readiness() -> dict[str, Any]:
    """Readiness probe: confirm the Dental persistence backend is reachable."""
    try:
        store = get_dental_store()
        if not store.healthcheck():
            raise RuntimeError("database probe returned no result")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "component": "dental_database",
                "message": "Dental database is unavailable",
            },
        ) from exc
    return {
        "status": "ready",
        "component": "dental_api",
        "database": store.backend,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "universal-intelligence-engine",
        "version": app.version,
        "providers": {
            "knowledge": True,
            "reasoning": True,
            "knowledge_provider": "wikipedia+europe-pmc-public-api",
            "knowledge_mode": "best-effort-public-sources",
            "knowledge_limitations": "Not broad web search; evidence may be empty if public sources are unavailable or return no results.",
            "reasoning_provider": (
                "openai-responses" if os.getenv("OPENAI_API_KEY") else "native"
            ),
            "reasoning_model": (
                "native"
                if not os.getenv("OPENAI_API_KEY")
                else os.getenv("OPENAI_MODEL") or "default"
            ),
        },
        "dental_api": True,
    }


@app.post("/v1/intelligence/run")
def run_intelligence(
    body: IntelligenceRunBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        result = service.execute(
            IntelligenceRequest(
                goal=body.goal,
                context=body.context,
                locale=body.locale,
                language=body.language,
                constraints=body.constraints,
            )
        )
    except ProviderNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Provider is not configured.",
                "code": exc.code,
                "provider": exc.provider,
            },
        ) from exc
    except ProviderRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "AI is busy right now. Please try again shortly.",
                "code": exc.code,
                "provider": exc.provider,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Provider request failed.",
                "code": "PROVIDER_REQUEST_FAILED",
            },
        ) from exc
    return {
        "answer": result.answer,
        "evidence": [item.__dict__ for item in result.evidence],
        "actions": list(result.actions),
        "confidence": result.confidence,
        "metadata": dict(result.metadata),
    }


# Temporary, opt-in staging smoke check. Disabled by default and removed after
# the one-time authentication/workspace verification.
@app.get("/__staging/smoke")
async def oralyn_staging_smoke():
    if os.getenv("ORALYN_STAGING_SMOKE_TEST", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")

    email = os.getenv("DENTAL_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.getenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        raise HTTPException(status_code=503, detail="Staging bootstrap credentials are not configured")

    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://oralyn-staging-smoke",
        timeout=20,
    ) as client:
        login = await client.post(
            "/v1/dental/auth/password/login",
            json={"email": email, "password": password},
        )
        if login.status_code != 200:
            return {
                "status": "failed",
                "database": get_dental_store().backend,
                "login_http": login.status_code,
                "dashboard_access": False,
            }

        payload = login.json()
        token = str(payload.get("access_token") or "")
        user = payload.get("user") or {}
        headers = {"Authorization": f"Bearer {token}"}

        workspaces = await client.get("/v1/dental/auth/workspaces", headers=headers)
        me = await client.get("/v1/dental/auth/me", headers=headers)
        patients = await client.get("/v1/dental/patients", headers=headers)
        appointments = await client.get("/v1/dental/appointments", headers=headers)
        logout = await client.post("/v1/dental/auth/logout", headers=headers)

    def rows(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("workspaces", "patients", "appointments", "items", "data"):
                if isinstance(value.get(key), list):
                    return value[key]
        return []

    workspace_rows = rows(workspaces.json()) if workspaces.status_code == 200 else []
    patient_rows = rows(patients.json()) if patients.status_code == 200 else []
    appointment_rows = rows(appointments.json()) if appointments.status_code == 200 else []
    passed = all(
        response.status_code == 200
        for response in (workspaces, me, patients, appointments, logout)
    ) and bool(token) and bool(workspace_rows)

    return {
        "status": "passed" if passed else "failed",
        "database": get_dental_store().backend,
        "login_http": login.status_code,
        "workspace_http": workspaces.status_code,
        "session_http": me.status_code,
        "patients_http": patients.status_code,
        "appointments_http": appointments.status_code,
        "logout_http": logout.status_code,
        "doctor_role": user.get("role"),
        "workspace_count": len(workspace_rows),
        "workspace_names": [str(row.get("name") or "") for row in workspace_rows],
        "workspace_types": [str(row.get("clinic_type") or row.get("workspace_type") or "") for row in workspace_rows],
        "patient_count": len(patient_rows),
        "appointment_count": len(appointment_rows),
        "dashboard_access": passed,
    }
