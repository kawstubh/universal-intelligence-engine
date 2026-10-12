
"""HTTP API for the Universal Intelligence Engine and Dental application layer."""
from __future__ import annotations
import hmac
import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from .contracts import IntelligenceRequest
from .dental_api import router as dental_router
from .dental_ai import router as dental_ai_router
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .memory import LearningMemory
from .reasoning import NativeReasoningProvider
from .providers import BraveSearchProvider, DisabledKnowledgeProvider, NoKnowledgeProvider, OpenAIResponsesReasoningProvider, ProviderNotConfiguredError, ProviderRateLimitError
from .service import IntelligenceService

class IntelligenceRunBody(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    context: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None
    language: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)

def build_service() -> IntelligenceService:
    knowledge = BraveSearchProvider() if os.getenv("BRAVE_SEARCH_API_KEY") else NoKnowledgeProvider()
    reasoning = OpenAIResponsesReasoningProvider() if os.getenv("OPENAI_API_KEY") else NativeReasoningProvider()
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
    version=__import__("universal_intelligence_engine").__version__,
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
            content={"detail": {"code": "PATIENT_AI_DISABLED", "message": "Patient-facing AI is not available."}},
        )
    return await call_next(request)

app.include_router(dental_router)
app.include_router(dental_ai_router)

def _doctor_only_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema["paths"] = {
        path: operations
        for path, operations in schema.get("paths", {}).items()
        if not (path.rstrip("/") == "/v1/dental/ai/patient" or path.startswith("/v1/dental/ai/patient/"))
    }
    app.openapi_schema = schema
    return schema

app.openapi = _doctor_only_openapi

def _authorize(authorization: str | None) -> None:
    expected = os.getenv("UIE_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="UIE_API_KEY is not configured; API access is fail-closed.")
    supplied = authorization or ""
    expected_header = f"Bearer {expected}"
    if not hmac.compare_digest(supplied, expected_header):
        raise HTTPException(status_code=401, detail="Invalid API credentials")

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "universal-intelligence-engine",
        "version": app.version,
        "providers": {
            "knowledge": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
            "reasoning": bool(
                (
                    {
                        "openai": os.getenv("OPENAI_API_KEY"),
                        "gemini": os.getenv("GEMINI_API_KEY"),
                        "groq": os.getenv("GROQ_API_KEY"),
                    }.get(os.getenv("REASONING_PROVIDER", "openai").strip().lower())
                    or os.getenv("REASONING_API_KEY")
                )
                and (os.getenv("REASONING_MODEL") or os.getenv("OPENAI_MODEL"))
            ),
            "knowledge_provider": "brave-search",
            "reasoning_provider": os.getenv("REASONING_PROVIDER", "openai"),
            "reasoning_model": os.getenv("REASONING_MODEL") or os.getenv("OPENAI_MODEL"),
        },
        "dental_api": True,
    }

@app.post("/v1/intelligence/run")
def run_intelligence(body: IntelligenceRunBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    try:
        result = service.execute(IntelligenceRequest(
            goal=body.goal, context=body.context, locale=body.locale,
            language=body.language, constraints=body.constraints
        ))
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc), "code": exc.code, "provider": exc.provider}) from exc
    except ProviderRateLimitError as exc:
        raise HTTPException(status_code=429, detail={"message": "AI is busy right now. Please try again shortly.", "code": exc.code, "provider": exc.provider}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail={"message": str(exc), "code": "PROVIDER_REQUEST_FAILED"}) from exc
    return {
        "answer": result.answer,
        "evidence": [item.__dict__ for item in result.evidence],
        "actions": list(result.actions),
        "confidence": result.confidence,
        "metadata": dict(result.metadata),
    }

