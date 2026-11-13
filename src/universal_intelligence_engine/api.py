"""HTTP API for the Universal Intelligence Engine and Dental application layer."""

from __future__ import annotations

import hmac
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .contracts import IntelligenceRequest, KnowledgeProvider
from .dental_ai import router as dental_ai_router
from .dental_api import router as dental_router
from .dental_assistant import router as dental_assistant_router
from .dental_documents import router as dental_documents_router
from .dental_patient_care import router as dental_patient_care_router
from .dental_patient_comms import router as dental_patient_comms_router
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
def _parse_cors_origins(raw: str | None) -> list[str]:
    """Parse a strict allow-list of exact browser origins.

    An unset value deliberately returns an empty list: deployed services must
    opt in to their exact HTTPS portal origin instead of inheriting localhost
    or LAN development origins.
    """
    if not raw:
        return []

    origins: list[str] = []
    for candidate in raw.split(","):
        origin = candidate.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("DENTAL_CORS_ORIGINS contains an invalid port.") from exc
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        if (
            scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError(
                "DENTAL_CORS_ORIGINS must contain exact HTTP(S) origins only; "
                "wildcards, credentials, paths, queries and fragments are not allowed."
            )
        host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
        is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        if port is not None and not is_default_port:
            host = f"{host}:{port}"
        normalized = f"{scheme}://{host}"
        if origin != normalized:
            raise ValueError(
                "DENTAL_CORS_ORIGINS entries must use canonical origin form without a trailing slash."
            )
        origins.append(normalized)
    return list(dict.fromkeys(origins))


origins = _parse_cors_origins(os.getenv("DENTAL_CORS_ORIGINS"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    expose_headers=[
        "Cache-Control",
        "Pragma",
        "Expires",
        "X-Content-Type-Options",
        "Strict-Transport-Security",
        "Referrer-Policy",
        "X-Frame-Options",
        "Permissions-Policy",
    ],
)

service = build_service()


# Launch policy: patient-facing AI is disabled at the API boundary, including
# legacy routes that may still be registered by the Dental router.
@app.middleware("http")
async def disable_patient_ai(request: Request, call_next):
    path = request.url.path.rstrip("/")
    disabled_patient_ai = {
        "/v1/dental/ai/patient/chat",
        "/v1/dental/ai/patient/triage",
        "/v1/dental/ai/patient/treatment-explanation",
        "/v1/dental/ai/patient/aftercare",
        # Consent and stored AI history are part of the patient AI surface too.
        "/v1/dental/ai/patient/consent",
        "/v1/dental/ai/patient/history",
    }
    if path in disabled_patient_ai:
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


@app.middleware("http")
async def secure_dental_response_headers(request: Request, call_next):
    """Prevent browser, proxy and service-worker caches from retaining clinical API data."""
    response = await call_next(request)
    if request.url.path == "/v1/dental" or request.url.path.startswith("/v1/dental/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    )
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    return response


app.include_router(dental_router)
app.include_router(dental_assistant_router)
app.include_router(dental_ai_router)
app.include_router(dental_patient_care_router)
app.include_router(dental_documents_router)
app.include_router(dental_patient_comms_router)


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
        if not path.rstrip("/").startswith(
            ("/v1/dental/patient/", "/v1/dental/ai/patient/")
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
