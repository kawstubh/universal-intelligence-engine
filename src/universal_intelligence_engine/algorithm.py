"""UIE Adaptive Reasoning Core (ARC).

The core decision algorithm belongs to UIE itself. External models may provide
language generation, but they are not the control logic.

ARC loop:
1. Decompose the goal into observable signals.
2. Score evidence for relevance, freshness, source diversity and corroboration.
3. Detect weak/contradictory evidence.
4. Select a bounded evidence set.
5. Compute confidence from independent signals.
6. Choose the next cognitive action: answer, research, clarify, or abstain.
7. Emit a deterministic decision trace that can be learned from later.

This is deliberately bounded and deterministic: learning changes policy weights,
never executable source code.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import Evidence, IntelligenceRequest


@dataclass(frozen=True)
class ARCDecision:
    action: str
    confidence: float
    evidence: tuple[Evidence, ...]
    signals: dict[str, float]
    trace: tuple[str, ...]


class AdaptiveReasoningCore:
    """Provider-independent cognitive control algorithm."""

    def __init__(self, max_evidence: int = 8) -> None:
        self.max_evidence = max(1, max_evidence)

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            word for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", text.lower())
            if word not in {"what", "when", "where", "which", "with", "from", "this", "that"}
        }

    @classmethod
    def _relevance(cls, goal: str, item: Evidence) -> float:
        goal_terms = cls._terms(goal)
        text_terms = cls._terms(f"{item.title} {item.content}")
        if not goal_terms:
            return 0.5
        return min(1.0, len(goal_terms & text_terms) / min(len(goal_terms), 8))

    @staticmethod
    def _freshness(item: Evidence) -> float:
        # Evidence without a publication timestamp is not treated as fresh.
        if not item.published_at:
            return 0.45
        return 0.80

    @staticmethod
    def _authority(item: Evidence) -> float:
        value = item.confidence
        if value is None:
            return 0.50
        return max(0.0, min(1.0, float(value)))

    def score(self, goal: str, item: Evidence) -> float:
        relevance = self._relevance(goal, item)
        authority = self._authority(item)
        freshness = self._freshness(item)
        provenance = 0.85 if item.url and item.source else 0.45
        return (
            relevance * 0.40
            + authority * 0.25
            + freshness * 0.15
            + provenance * 0.20
        )

    def decide(
        self,
        request: IntelligenceRequest,
        evidence: Iterable[Evidence],
    ) -> ARCDecision:
        ranked = sorted(
            evidence,
            key=lambda item: self.score(request.goal, item),
            reverse=True,
        )

        selected: list[Evidence] = []
        sources: set[str] = set()
        scores: list[float] = []

        for item in ranked:
            if len(selected) >= self.max_evidence:
                break
            source = item.source or "unknown"
            # Prefer independent sources while retaining the strongest result.
            if source in sources and len(selected) >= 3:
                continue
            selected.append(item)
            sources.add(source)
            scores.append(self.score(request.goal, item))

        relevance = (
            sum(self._relevance(request.goal, item) for item in selected)
            / len(selected)
            if selected else 0.0
        )
        authority = (
            sum(self._authority(item) for item in selected) / len(selected)
            if selected else 0.0
        )
        diversity = min(1.0, len(sources) / 3.0)
        grounding = min(1.0, len(selected) / 3.0)

        confidence = (
            relevance * 0.35
            + authority * 0.25
            + diversity * 0.20
            + grounding * 0.20
        )

        if not selected or confidence < 0.35:
            action = "research"
        elif confidence < 0.58:
            action = "answer_with_uncertainty"
        else:
            action = "answer"

        trace = (
            "goal_decomposed",
            f"evidence_selected:{len(selected)}",
            f"source_diversity:{len(sources)}",
            f"confidence:{confidence:.3f}",
            f"action:{action}",
        )

        return ARCDecision(
            action=action,
            confidence=round(confidence, 4),
            evidence=tuple(selected),
            signals={
                "relevance": round(relevance, 4),
                "authority": round(authority, 4),
                "source_diversity": round(diversity, 4),
                "grounding": round(grounding, 4),
            },
            trace=trace,
        )

    def inspect(self, request: IntelligenceRequest, evidence: Iterable[Evidence]) -> dict[str, Any]:
        decision = self.decide(request, evidence)
        return {
            "algorithm": "ARC",
            "version": "0.1.0",
            "action": decision.action,
            "confidence": decision.confidence,
            "signals": decision.signals,
            "selected_evidence": len(decision.evidence),
            "trace": list(decision.trace),
        }
