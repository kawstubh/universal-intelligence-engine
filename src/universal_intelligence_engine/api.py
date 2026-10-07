
"""HTTP API for the Universal Intelligence Engine and Dental application layer."""
from __future__ import annotations
import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from .contracts import IntelligenceRequest
from .dental_api import router as dental_router
from .dental_ai import router as dental_ai_router
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .memory import LearningMemory
from .providers import BraveSearchProvider, OpenAIResponsesReasoningProvider, ProviderNotConfiguredError
from .service import IntelligenceService

class IntelligenceRunBody(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    context: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None
    language: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)

def build_service() -> IntelligenceService:
    knowledge = BraveSearchProvider()
    reasoning = OpenAIResponsesReasoningProvider()
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
    version="0.5.0",
    description="Shared intelligence API for research, reasoning, applications, agents, and Dental Intelligence.",
)
service = build_service()
app.include_router(dental_router)
app.include_router(dental_ai_router)

def _authorize(authorization: str | None) -> None:
    expected = os.getenv("UIE_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="UIE_API_KEY is not configured; API access is fail-closed.")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API credentials")

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "universal-intelligence-engine",
        "version": "0.5.0",
        "providers": {
            "knowledge": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
            "reasoning": bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL")),
            "knowledge_provider": "brave-search",
            "reasoning_provider": "openai-responses",
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
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail={"message": str(exc), "code": "PROVIDER_REQUEST_FAILED"}) from exc
    return {
        "answer": result.answer,
        "evidence": [item.__dict__ for item in result.evidence],
        "actions": list(result.actions),
        "confidence": result.confidence,
        "metadata": dict(result.metadata),
    }

