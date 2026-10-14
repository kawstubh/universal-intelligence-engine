"""Reasoning-facing claim retrieval and optional non-blocking gap-triggered learning."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contradiction import ContradictionEngine


class ClaimQueryService:
    """Read structured claim memory without upgrading unverified claims to facts."""

    def __init__(self, memory: ContradictionEngine | None = None, db_path: str | Path | None = None, learner=None):
        self.memory = memory or ContradictionEngine(str(db_path or os.getenv("UIE_CLAIM_MEMORY_PATH", ".data/claim_memory.sqlite3")))
        self.learner = learner

    def query(self, subject: str | None = None, limit: int = 20, trigger_learning: bool = False) -> dict[str, Any]:
        """Return claim records; optionally schedule one bounded background cycle for unresolved queries."""
        limit = max(1, min(int(limit), 100))
        claims = self.memory.get_claims(subject=subject)
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


def query_claims(subject: str | None = None, limit: int = 20, memory: ContradictionEngine | None = None, trigger_learning: bool = False, learner=None) -> dict[str, Any]:
    """Functional hook for reasoning code and tests."""
    return ClaimQueryService(memory=memory, learner=learner).query(subject=subject, limit=limit, trigger_learning=trigger_learning)
