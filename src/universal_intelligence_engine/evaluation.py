"""Deterministic production evaluation primitives."""

from __future__ import annotations

from .contracts import IntelligenceResponse


class BasicResponseEvaluator:
    """Score response completeness and evidence grounding without claiming truth."""

    def evaluate(self, response: IntelligenceResponse) -> dict[str, object]:
        answer = str(response.answer or "").strip()
        evidence_count = len(response.evidence)
        has_sources = evidence_count > 0
        checks = {
            "non_empty_answer": bool(answer),
            "has_evidence": has_sources,
            "has_confidence_or_evidence": (
                response.confidence is not None or has_sources
            ),
        }
        passed = all(checks.values())
        score = sum(bool(value) for value in checks.values()) / len(checks)
        return {
            "passed": passed,
            "score": round(score, 3),
            "checks": checks,
            "evidence_count": evidence_count,
            "evaluation_type": "deterministic-baseline",
        }
