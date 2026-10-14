"""HTTP API for running UIE as a shared intelligence service."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .contracts import IntelligenceRequest
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .memory import LearningMemory
from .persistent_memory import PersistentMemoryEngine
from .providers import FreePublicKnowledgeProvider, OpenAIResponsesReasoningProvider
from .web_knowledge import PublicWebKnowledgeStore
from .ingestion.pipeline import CrossDomainIngestionPipeline
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


class KnowledgeHarvestBody(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)


def build_service() -> IntelligenceService:
    web_knowledge = PublicWebKnowledgeStore(
        os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3")
    )
    knowledge = FreePublicKnowledgeProvider(web_knowledge=web_knowledge)
    # The engine provides native reasoning when no optional OpenAI key is set.
    reasoning = OpenAIResponsesReasoningProvider() if os.getenv("OPENAI_API_KEY") else None
    memory = LearningMemory(
        os.getenv("UIE_MEMORY_PATH", ".data/learning.jsonl")
    )
    engine = UniversalIntelligenceEngine(
        knowledge=knowledge,
        reasoning=reasoning,
        evaluator=BasicResponseEvaluator(),
        learning_store=memory,
        lesson_memory=PersistentMemoryEngine(
            os.getenv("UIE_PERSISTENT_MEMORY_PATH", ".data/persistent_memory.sqlite3")
        ),
    )
    return IntelligenceService(engine)


app = FastAPI(
    title="Universal Intelligence Engine",
    version="0.5.0",
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
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API credentials")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "universal-intelligence-engine",
        "version": "0.5.0",
        "providers": {
            "knowledge": True,
            "knowledge_provider": "wikipedia+europe-pmc-public-api",
            "knowledge_mode": "best-effort-public-sources",
            "knowledge_limitations": "Not broad web search; evidence may be empty if public sources are unavailable or return no results.",
            "reasoning": True,
            "reasoning_provider": "openai-responses" if os.getenv("OPENAI_API_KEY") else "native",
            "reasoning_model": os.getenv("OPENAI_MODEL", "native") if os.getenv("OPENAI_API_KEY") else "native",
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


@app.get("/v1/knowledge/stats")
def knowledge_stats(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    store = PublicWebKnowledgeStore(
        os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3")
    )
    from .learning.contradiction import ContradictionEngine
    claims = ContradictionEngine(os.getenv("UIE_CLAIM_MEMORY_PATH", ".data/claim_memory.sqlite3"))
    return {"status": "ok", "corpus": store.stats(),
            "claims": {"total": len(claims.get_claims()), "contradictions": len(claims.contradictions())},
            "mode": "bounded-public-web-harvesting-and-claim-distillation",
            "limitations": ["approved-domain allowlist", "robots.txt required",
                            "rule-based extraction; claims remain unverified", "no model-weight training"]}


@app.post("/v1/knowledge/queue/seed")
def seed_knowledge_queue(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    store = PublicWebKnowledgeStore(os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3"))
    pipeline = CrossDomainIngestionPipeline(store, os.getenv("UIE_INGESTION_QUEUE_PATH", ".data/ingestion_queue.sqlite3"))
    return {"status": "ok", **pipeline.seed(), "queue": pipeline.stats()}


@app.post("/v1/knowledge/queue/discover")
def discover_knowledge_queue(
    max_sources: int = 3,
    max_urls_per_source: int = 20,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = PublicWebKnowledgeStore(os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3"))
    pipeline = CrossDomainIngestionPipeline(store, os.getenv("UIE_INGESTION_QUEUE_PATH", ".data/ingestion_queue.sqlite3"))
    return {"status": "ok", **pipeline.discover(max_sources, max_urls_per_source)}


@app.post("/v1/knowledge/queue/run")
def run_knowledge_queue(
    limit: int = 5,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = PublicWebKnowledgeStore(os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3"))
    pipeline = CrossDomainIngestionPipeline(store, os.getenv("UIE_INGESTION_QUEUE_PATH", ".data/ingestion_queue.sqlite3"))
    return {"status": "ok", **pipeline.run_batch(limit)}


@app.post("/v1/knowledge/harvest")
def harvest_public_knowledge(
    body: KnowledgeHarvestBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = PublicWebKnowledgeStore(
        os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3")
    )
    results = []
    for url in body.urls:
        try:
            results.append(store.ingest_url(url))
        except ValueError as exc:
            results.append({"url": url, "status": "rejected", "reason": str(exc)})
    return {"results": results, "corpus": store.stats()}
