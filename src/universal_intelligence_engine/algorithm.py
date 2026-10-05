"""UIE Adaptive Reasoning Core (ARC).

Deterministic cognitive control: evidence selection, freshness, source
diversity, contradiction detection, confidence and action selection.
External models may generate language, but they do not own the control logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import Evidence, IntelligenceRequest
from .temporal import detect_contradictions, temporal_assessment


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
    def _authority(item: Evidence) -> float:
        value = item.confidence
        return 0.50 if value is None else max(0.0, min(1.0, float(value)))

    def score(self, goal: str, item: Evidence) -> float:
        relevance = self._relevance(goal, item)
        authority = self._authority(item)
        temporal = temporal_assessment(item)
        provenance = 0.85 if item.url and item.source else 0.45
        return (
            relevance * 0.40
            + authority * 0.25
            + temporal.freshness * 0.15
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

        for item in ranked:
            if len(selected) >= self.max_evidence:
                break
            source = item.source or "unknown"
            if source in sources and len(selected) >= 3:
                continue
            selected.append(item)
            sources.add(source)

        conflicts = detect_contradictions(selected)

        relevance = (
            sum(self._relevance(request.goal, item) for item in selected) / len(selected)
            if selected else 0.0
        )
        authority = (
            sum(self._authority(item) for item in selected) / len(selected)
            if selected else 0.0
        )
        diversity = min(1.0, len(sources) / 3.0)
        grounding = min(1.0, len(selected) / 3.0)
        freshness = (
            sum(temporal_assessment(item).freshness for item in selected) / len(selected)
            if selected else 0.0
        )
        contradiction = (
            min(1.0, sum(c.severity for c in conflicts) / max(1, len(conflicts)))
            if conflicts else 0.0
        )

        confidence = (
            relevance * 0.30
            + authority * 0.20
            + diversity * 0.15
            + grounding * 0.15
            + freshness * 0.10
            + (1.0 - contradiction) * 0.10
        )

        if not selected or confidence < 0.35:
            action = "research"
        elif conflicts or confidence < 0.58:
            action = "answer_with_uncertainty"
        else:
            action = "answer"

        trace = (
            "goal_decomposed",
            f"evidence_selected:{len(selected)}",
            f"source_diversity:{len(sources)}",
            f"freshness:{freshness:.3f}",
            f"contradictions:{len(conflicts)}",
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
                "freshness": round(freshness, 4),
                "contradiction": round(contradiction, 4),
            },
            trace=trace,
        )

    def inspect(self, request: IntelligenceRequest, evidence: Iterable[Evidence]) -> dict[str, Any]:
        decision = self.decide(request, evidence)
        return {
            "algorithm": "ARC",
            "version": "0.2.0",
            "action": decision.action,
            "confidence": decision.confidence,
            "signals": decision.signals,
            "selected_evidence": len(decision.evidence),
            "trace": list(decision.trace),
        }
