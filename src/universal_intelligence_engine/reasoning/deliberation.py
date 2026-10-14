"""Bounded test-time deliberation over evidence-grounded reasoning trajectories.

Trajectories are structured evidence-selection paths, not private free-form chain-of-thought.
The process reward model scores relevance, evidence support, confidence, diversity,
and contradictions before a provider writes the user-facing synthesis.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re
from typing import Sequence

from ..contracts import Evidence, IntelligenceRequest, IntelligenceResponse, ReasoningProvider
from ..temporal import detect_contradictions, temporal_assessment


@dataclass(frozen=True)
class DeliberationBudget:
    depth: int
    beam_width: int
    enabled: bool
    reason: str


class ProcessRewardEvaluator:
    """Score each evidence step and complete trajectory with transparent, bounded signals."""

    _STOP = {"the", "and", "for", "with", "that", "this", "from", "what", "when", "where", "which", "does", "about", "into", "between"}

    @classmethod
    def terms(cls, text: str) -> set[str]:
        return {word for word in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()) if word not in cls._STOP}

    def score_step(self, goal: str, evidence: Evidence, prior_sources: set[str]) -> tuple[float, dict]:
        goal_terms = self.terms(goal)
        text_terms = self.terms(f"{evidence.title} {evidence.content}")
        relevance = len(goal_terms & text_terms) / max(1, len(goal_terms))
        confidence = max(0.0, min(1.0, float(evidence.confidence if evidence.confidence is not None else 0.5)))
        freshness = max(0.0, min(1.0, float(temporal_assessment(evidence).freshness)))
        novelty = 0.15 if evidence.source and evidence.source not in prior_sources else 0.0
        # Reward grounding and relevance; no reward for claims that cannot be tied to evidence.
        score = max(0.0, min(1.0, 0.50 * relevance + 0.30 * confidence + 0.15 * freshness + novelty))
        return score, {"source": evidence.source, "relevance": round(relevance, 4), "confidence": round(confidence, 4),
                       "freshness": round(freshness, 4), "grounded": bool(evidence.content.strip()), "step_reward": round(score, 4)}

    def score_trajectory(self, goal: str, path: Sequence[Evidence]) -> tuple[float, list[dict], dict]:
        steps = []
        sources: set[str] = set()
        rewards = []
        for evidence in path:
            reward, step = self.score_step(goal, evidence, sources)
            rewards.append(reward)
            steps.append(step)
            if evidence.source:
                sources.add(evidence.source)
        contradictions = len(detect_contradictions(path))
        mean_reward = sum(rewards) / max(1, len(rewards))
        coverage = len(set().union(*(self.terms(f"{e.title} {e.content}") for e in path)) & self.terms(goal)) / max(1, len(self.terms(goal)))
        diversity = min(1.0, len(sources) / max(1, len(path)))
        penalty = min(0.65, contradictions * 0.22)
        score = max(0.0, min(1.0, 0.62 * mean_reward + 0.23 * coverage + 0.15 * diversity - penalty))
        return score, steps, {"mean_step_reward": round(mean_reward, 4), "goal_coverage": round(coverage, 4),
                              "source_diversity": round(diversity, 4), "contradictions": contradictions,
                              "contradiction_penalty": round(penalty, 4), "verified": bool(path) and all(s["grounded"] for s in steps) and contradictions == 0}


class DeliberationController:
    """Bounded beam search that spends more test-time compute on complex queries."""

    def __init__(self, evaluator: ProcessRewardEvaluator | None = None, *, max_depth: int = 4, max_beam_width: int = 6):
        self.evaluator = evaluator or ProcessRewardEvaluator()
        self.max_depth = max(1, min(int(max_depth), 5))
        self.max_beam_width = max(2, min(int(max_beam_width), 8))

    def budget(self, request: IntelligenceRequest, evidence: Sequence[Evidence]) -> DeliberationBudget:
        goal = request.goal.strip()
        conflicts = len(detect_contradictions(evidence))
        words = ProcessRewardEvaluator.terms(goal)
        explicit_complexity = any(term in goal.lower() for term in ("compare", "versus", "vs", "why", "trade-off", "tradeoff", "across", "conflict", "contradict", "step-by-step", "multi-step"))
        cross_domain = len({str(e.metadata.get("domain", "")) for e in evidence if e.metadata.get("domain")}) > 1
        complex_task = explicit_complexity or conflicts > 0 or cross_domain or len(words) >= 9 or len(evidence) >= 6
        if not complex_task:
            return DeliberationBudget(1, 1, False, "direct_lookup")
        depth = min(self.max_depth, 2 + int(conflicts > 0) + int(cross_domain or len(words) >= 14))
        width = min(self.max_beam_width, 3 + min(len(evidence), 3) + int(conflicts > 0))
        return DeliberationBudget(depth, width, True, "conflict_or_multi_step_query" if conflicts or explicit_complexity else "high_complexity_or_cross_domain")

    def search(self, request: IntelligenceRequest, evidence: Sequence[Evidence], budget: DeliberationBudget | None = None) -> dict:
        chosen_budget = budget or self.budget(request, evidence)
        if not chosen_budget.enabled or len(evidence) < 2:
            return {"enabled": False, "budget": chosen_budget.__dict__, "trajectory_count": 0, "selected_evidence": list(evidence),
                    "selected_score": None, "trajectories": [], "selection_reason": "direct_lookup_or_insufficient_evidence"}
        ranked = sorted(evidence, key=lambda e: (float(e.confidence if e.confidence is not None else 0.5), len(ProcessRewardEvaluator.terms(e.title + " " + e.content))), reverse=True)
        max_path = min(chosen_budget.depth, len(ranked))
        candidates: list[tuple[Evidence, ...]] = []
        # Generate bounded evidence paths; combinations avoid factorial blow-ups and keep work deterministic.
        for size in range(2, max_path + 1):
            candidates.extend(combinations(ranked[:min(len(ranked), chosen_budget.beam_width + 1)], size))
            if len(candidates) >= chosen_budget.beam_width * 4:
                break
        scored = []
        for index, path in enumerate(candidates[:chosen_budget.beam_width * 4]):
            score, steps, signals = self.evaluator.score_trajectory(request.goal, path)
            scored.append({"trajectory_id": index + 1, "score": round(score, 4), "steps": steps, "signals": signals,
                           "evidence": list(path), "verified": signals["verified"]})
        verified = [item for item in scored if item["verified"]]
        pool = verified or scored
        selected = max(pool, key=lambda item: (item["score"], item["signals"]["goal_coverage"], len(item["evidence"]))) if pool else None
        return {"enabled": True, "budget": chosen_budget.__dict__, "trajectory_count": len(scored),
                "selected_evidence": selected["evidence"] if selected else list(evidence),
                "selected_score": selected["score"] if selected else None,
                "selected_trajectory_id": selected["trajectory_id"] if selected else None,
                "selected_verified": selected["verified"] if selected else False,
                "selection_reason": "highest_reward_verified_trajectory" if verified else "highest_reward_available_trajectory",
                "trajectories": [{k: v for k, v in item.items() if k != "evidence"} for item in scored]}


class UIEReasoningCoordinator:
    """Coordinate bounded deliberation and final synthesis through a pluggable provider."""

    def __init__(self, controller: DeliberationController | None = None):
        self.controller = controller or DeliberationController()

    def reason(self, request: IntelligenceRequest, evidence: Sequence[Evidence], provider: ReasoningProvider) -> IntelligenceResponse:
        search = self.controller.search(request, evidence)
        selected = search["selected_evidence"]
        response = provider.reason(request, selected)
        metadata = dict(response.metadata)
        metadata["deliberation"] = {k: v for k, v in search.items() if k not in {"selected_evidence"}}
        metadata["deliberation"]["input_evidence_count"] = len(evidence)
        metadata["deliberation"]["selected_evidence_count"] = len(selected)
        metadata["reasoning_mode"] = "test_time_deliberation" if search["enabled"] else "direct"
        return IntelligenceResponse(answer=response.answer, evidence=response.evidence or selected, actions=response.actions,
                                    confidence=response.confidence, metadata=metadata)
