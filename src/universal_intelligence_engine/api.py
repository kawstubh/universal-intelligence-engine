"""HTTP API for running UIE as a shared intelligence service."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__

from .contracts import IntelligenceRequest
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .memory import LearningMemory
from .providers import BraveSearchProvider, NoKnowledgeProvider, OpenAIResponsesReasoningProvider
from .reasoning import NativeReasoningProvider
from .service import IntelligenceService


class IntelligenceRunBody(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    context: dict[str, Any] = Field(default_factory=dict)
    locale: str | None = None
    language: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    autonomous: bool = False
    max_iterations: int = Field(default=3, ge=1, le=8)
    target_confidence: float = Field(default=0.72, ge=0.0, le=1.0)


def build_service() -> IntelligenceService:
    # Provider selection is explicit: the core remains usable offline, but never
    # invents evidence when the live research provider is not configured.
    knowledge = (
        BraveSearchProvider()
        if os.getenv("BRAVE_SEARCH_API_KEY")
        else NoKnowledgeProvider()
    )
    reasoning = (
        OpenAIResponsesReasoningProvider()
        if os.getenv("OPENAI_API_KEY")
        else NativeReasoningProvider()
    )
    memory = LearningMemory(
        os.getenv("UIE_MEMORY_PATH", ".data/learning.jsonl")
    )
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
    description="Shared intelligence API for research, reasoning, applications and agents.",
)
service = build_service()


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


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "universal-intelligence-engine",
        "version": __version__,
        "providers": {
            "knowledge": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
            "reasoning": bool(os.getenv("OPENAI_API_KEY")),
        },
    }


@app.post("/v1/intelligence/run")
def run_intelligence(
    body: IntelligenceRunBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        request = IntelligenceRequest(
            goal=body.goal,
            context=body.context,
            locale=body.locale,
            language=body.language,
            constraints=body.constraints,
        )
        result = (
            service.engine.run_autonomous(
                request,
                max_iterations=body.max_iterations,
                target_confidence=body.target_confidence,
            )
            if body.autonomous
            else service.execute(request)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "answer": result.answer,
        "evidence": [item.__dict__ for item in result.evidence],
        "actions": list(result.actions),
        "confidence": result.confidence,
        "metadata": dict(result.metadata),
    }
