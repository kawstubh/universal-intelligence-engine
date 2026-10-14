"""Reasoning-facing claim retrieval and optional non-blocking gap-triggered learning."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contradiction import ContradictionEngine
from .semantic_matcher import SemanticMatcher
from .graph_store import KnowledgeGraphStore


class ClaimQueryService:
    """Read structured claim memory without upgrading unverified claims to facts."""

    def __init__(self, memory: ContradictionEngine | None = None, db_path: str | Path | None = None, learner=None):
        self.memory = memory or ContradictionEngine(str(db_path or os.getenv("UIE_CLAIM_MEMORY_PATH", ".data/claim_memory.sqlite3")))
        self.learner = learner

    def query(self, subject: str | None = None, limit: int = 20, trigger_learning: bool = False,
              include_graph: bool = True, max_hops: int = 2) -> dict[str, Any]:
        """Return claim records; optionally schedule one bounded background cycle for unresolved queries."""
        limit = max(1, min(int(limit), 100))
        all_claims = self.memory.get_claims()
        if subject:
            exact = [c for c in all_claims if subject.lower() in c.get("subject","").lower()]
            if exact:
                claims = exact
            else:
                claims = SemanticMatcher(threshold=0.18).match(subject, all_claims, threshold=0.18, limit=limit)
        else:
            claims = all_claims
        claims = claims[-limit:]
        warnings = [
            {"claim_id": claim["id"], "status": claim["status"], "warning": claim["warning"]}
            for claim in claims if claim.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}
        ]
        response = {
            "subject_query": subject,
            "count": len(claims),
            "claims": [
                {key: claim.get(key) for key in (
                    "id", "subject", "predicate", "object_claim", "temporal_anchor", "confidence",
                    "provenance_url", "provenance_urls", "domain", "status", "warning", "created_at", "updated_at"
                )}
                for claim in claims
            ],
            "warnings": warnings,
            "usage_warning": "Extracted claims are evidence candidates, not verified facts. Check provenance and temporal scope before reasoning from them.",
        }
        if include_graph and subject:
            graph = KnowledgeGraphStore(self.memory.db_path).get_entity_subgraph(subject, max_hops=max_hops)
            graph_warnings = []
            for edge in graph.get("relations", []):
                if edge.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}:
                    graph_warnings.append({
                        "relation_id": edge.get("id"),
                        "status": edge.get("status"),
                        "warning": "Graph edge is evidence, not a verified fact.",
                        "provenance_url": edge.get("provenance_url"),
                    })
            response["knowledge_graph"] = graph
            response["graph_warnings"] = graph_warnings
        if trigger_learning and subject:
            if self.learner is None:
                from .autonomous_learner import AutonomousLearner
                self.learner = AutonomousLearner(claim_memory=self.memory)
            unresolved = not claims or bool(warnings)
            if unresolved:
                response["learning_trigger"] = self.learner.trigger_background(subject)
            else:
                response["learning_trigger"] = {"scheduled": False, "status": "knowledge_has_no_flagged_claims"}
        return response


def query_claims(subject: str | None = None, limit: int = 20, memory: ContradictionEngine | None = None,
                 trigger_learning: bool = False, learner=None, include_graph: bool = True,
                 max_hops: int = 2) -> dict[str, Any]:
    """Functional hook for reasoning code and tests."""
    return ClaimQueryService(memory=memory, learner=learner).query(
        subject=subject, limit=limit, trigger_learning=trigger_learning,
        include_graph=include_graph, max_hops=max_hops)
