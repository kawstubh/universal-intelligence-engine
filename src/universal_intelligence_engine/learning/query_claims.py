"""Reasoning-facing retrieval for distilled claims, with mandatory trust warnings."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contradiction import ContradictionEngine


class ClaimQueryService:
    """Read structured claim memory without upgrading unverified claims to facts."""

    def __init__(self, memory: ContradictionEngine | None = None, db_path: str | Path | None = None):
        self.memory = memory or ContradictionEngine(str(db_path or os.getenv("UIE_CLAIM_MEMORY_PATH", ".data/claim_memory.sqlite3")))

    def query(self, subject: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Return claim records, provenance and explicit warnings for risky statuses."""
        limit = max(1, min(int(limit), 100))
        claims = self.memory.get_claims(subject=subject)
        claims = claims[-limit:]
        warnings = [
            {"claim_id": claim["id"], "status": claim["status"], "warning": claim["warning"]}
            for claim in claims if claim.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}
        ]
        return {
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


def query_claims(subject: str | None = None, limit: int = 20, memory: ContradictionEngine | None = None) -> dict[str, Any]:
    """Functional hook for reasoning code and tests."""
    return ClaimQueryService(memory=memory).query(subject=subject, limit=limit)
