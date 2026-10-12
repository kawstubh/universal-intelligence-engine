"""Provider-independent native reasoning for UIE.

This module deliberately favors verifiable operations over fluent generation:
claim extraction, evidence aggregation, contradiction awareness, uncertainty,
and deterministic synthesis. External LLMs can later augment language quality
without owning the reasoning control plane.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Sequence

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .temporal import detect_contradictions, temporal_assessment


class NativeReasoningProvider:
    """Deterministic reasoning fallback that requires no external LLM."""

    def _sentences(self, text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]

    def _key_terms(self, text: str) -> list[str]:
        stop = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have"}
        return [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", text.lower()) if w not in stop]

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
    ) -> IntelligenceResponse:
        conflicts = detect_contradictions(evidence)
        ranked = sorted(
            evidence,
            key=lambda item: (
                float(item.confidence or 0.5),
                temporal_assessment(item).freshness,
            ),
            reverse=True,
        )

        goal_terms = Counter(self._key_terms(request.goal))
        candidates: list[tuple[float, str, Evidence]] = []
        for item in ranked:
            terms = set(self._key_terms(f"{item.title} {item.content}"))
            overlap = sum(goal_terms[t] for t in terms if t in goal_terms)
            for sentence in self._sentences(item.content)[:6]:
                if sentence:
                    candidates.append((overlap + float(item.confidence or 0.5), sentence, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected: list[tuple[str, Evidence]] = []
        seen: set[str] = set()
        for _, sentence, item in candidates:
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            selected.append((sentence, item))
            if len(selected) >= 5:
                break

        sources = []
        for _, item in selected:
            label = item.source or "unknown source"
            if label not in sources:
                sources.append(label)

        avg_conf = (
            sum(float(item.confidence or 0.5) for item in ranked[:5]) / min(len(ranked), 5)
            if ranked else 0.0
        )
        freshness = (
            sum(temporal_assessment(item).freshness for item in ranked[:5]) / min(len(ranked), 5)
            if ranked else 0.0
        )
        confidence = max(0.0, min(1.0, avg_conf * 0.65 + freshness * 0.35))
        if conflicts:
            confidence *= 0.65

        if not selected:
            answer = (
                "I cannot establish a supported answer from the available evidence. "
                "More research is required."
            )
        else:
            lines = [f"Goal: {request.goal}", "", "Evidence-supported findings:"]
            for sentence, item in selected:
                lines.append(f"- {sentence} [{item.source}]")
            if conflicts:
                lines.extend([
                    "",
                    "Uncertainty: the available evidence contains conflicting claims. "
                    "The conflict should be resolved before treating the conclusion as established.",
                ])
            else:
                lines.extend([
                    "",
                    "Assessment: this synthesis is limited to the retrieved evidence; "
                    "it does not invent unsupported facts.",
                ])
            answer = "\n".join(lines)

        return IntelligenceResponse(
            answer=answer,
            evidence=tuple(item for _, item in selected) or tuple(ranked),
            confidence=round(confidence, 4),
            metadata={
                "provider": "native",
                "native_reasoning": True,
                "external_llm_required": False,
                "source_count": len(sources),
                "contradiction_count": len(conflicts),
                "freshness": round(freshness, 4),
            },
        )
