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
from .tool_reflexion import ToolReflexionEngine
from ..learning.deep_reflexion import DeepReflexionEngine
from ..learning.epistemic_dispatcher import EpistemicDiscoveryDispatcher
from .symbolic_verifier import verify_numeric_claims


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
        # Keep the legacy grounded flag for source-attributed evidence, while the
        # continuous grounding score distinguishes source labels from verifiable citations.
        grounded = bool(evidence.content.strip() and (evidence.source or evidence.url or evidence.metadata.get("provenance")))
        logical_consistency = 0.0 if not evidence.content.strip() else 1.0
        domain_constraint_adherence = 1.0
        # The evaluator cannot certify clinical advice; it can only enforce explicit safety flags.
        if evidence.metadata.get("unsafe") or evidence.metadata.get("violates_constraints"):
            domain_constraint_adherence = 0.0
        numeric_findings = verify_numeric_claims(evidence.content)
        if numeric_findings:
            # Deterministic arithmetic contradictions cannot be treated as verified evidence.
            domain_constraint_adherence = 0.0
        evidence_grounding = (1.0 if evidence.content.strip() and (evidence.url or evidence.metadata.get("provenance"))
                              else (0.35 if evidence.content.strip() and evidence.source else 0.0))
        score = max(0.0, min(1.0, score * 0.75 + 0.10 * logical_consistency + 0.10 * evidence_grounding + 0.05 * domain_constraint_adherence))
        return score, {"source": evidence.source, "relevance": round(relevance, 4), "confidence": round(confidence, 4),
                       "freshness": round(freshness, 4), "grounded": grounded, "step_reward": round(score, 4),
                       "logical_consistency": logical_consistency, "evidence_grounding": round(evidence_grounding, 4),
                       "domain_constraint_adherence": domain_constraint_adherence,
                       "symbolic_numeric_findings": numeric_findings,
                       "symbolic_numeric_valid": not numeric_findings}

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
                              "contradiction_penalty": round(penalty, 4), "verified": bool(path) and all(s["grounded"] and s["domain_constraint_adherence"] == 1.0 for s in steps) and contradictions == 0,
                              "logical_consistency": round(sum(s["logical_consistency"] for s in steps) / max(1, len(steps)), 4),
                              "evidence_grounding": round(sum(s["evidence_grounding"] for s in steps) / max(1, len(steps)), 4),
                              "domain_constraint_adherence": round(sum(s["domain_constraint_adherence"] for s in steps) / max(1, len(steps)), 4)}


class DeliberationController:
    """Bounded beam search that spends more test-time compute on complex queries."""

    def __init__(self, evaluator: ProcessRewardEvaluator | None = None, *, max_depth: int = 4, max_beam_width: int = 6):
        self.evaluator = evaluator or ProcessRewardEvaluator()
        self.max_depth = max(1, min(int(max_depth), 5))
        self.max_beam_width = max(2, min(int(max_beam_width), 8))

    def budget(self, request: IntelligenceRequest, evidence: Sequence[Evidence]) -> DeliberationBudget:
        goal = request.goal.strip()
        evidence_conflicts = len(detect_contradictions(evidence))
        graph_conflicts = request.context.get("graph_conflicts", request.context.get("graph_contradictions", 0))
        try:
            graph_conflicts = len(graph_conflicts) if isinstance(graph_conflicts, (list, tuple, set)) else max(0, int(graph_conflicts))
        except (TypeError, ValueError):
            graph_conflicts = 0
        conflicts = evidence_conflicts + graph_conflicts
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

    def __init__(self, controller: DeliberationController | None = None, reflexion: ToolReflexionEngine | None = None, deep_reflexion: DeepReflexionEngine | None = None):
        self.controller = controller or DeliberationController()
        self.reflexion = reflexion or ToolReflexionEngine()
        self.deep_reflexion = deep_reflexion or DeepReflexionEngine()
        self.epistemic_dispatcher = EpistemicDiscoveryDispatcher(self.deep_reflexion.memory.db_path)

    def reason(self, request: IntelligenceRequest, evidence: Sequence[Evidence], provider: ReasoningProvider) -> IntelligenceResponse:
        safe_context = dict(request.context)
        selected_domain = safe_context.get("domain")
        procedural_rules = self.reflexion.applicable_rules(selected_domain if isinstance(selected_domain, str) else None)
        procedural_rules.extend(self.deep_reflexion.active_heuristics(selected_domain if isinstance(selected_domain, str) else None))
        procedural_rules = procedural_rules[:12]
        if procedural_rules:
            safe_context["uie_procedural_heuristics"] = procedural_rules
            request = IntelligenceRequest(goal=request.goal, context=safe_context, locale=request.locale,
                                         language=request.language, constraints=request.constraints)
        search = self.controller.search(request, evidence)
        selected = search["selected_evidence"]
        response = provider.reason(request, selected)
        metadata = dict(response.metadata)
        metadata["deliberation"] = {k: v for k, v in search.items() if k not in {"selected_evidence"}}
        metadata["deliberation"]["input_evidence_count"] = len(evidence)
        metadata["deliberation"]["selected_evidence_count"] = len(selected)
        metadata["reasoning_mode"] = "test_time_deliberation" if search["enabled"] else "direct"
        metadata["procedural_heuristics_applied"] = len(procedural_rules)
        selected_score = search.get("selected_score")
        selected_signals = next((item.get("signals", {}) for item in search.get("trajectories", [])
                                 if item.get("trajectory_id") == search.get("selected_trajectory_id")), {})
        grounding = float(selected_signals.get("evidence_grounding", 0.0) or 0.0)
        focus_entity = self.epistemic_dispatcher.focus_entity(request.goal, safe_context)
        graph_void = bool(safe_context.get("knowledge_graph_void") or safe_context.get("missing_graph_relations"))
        if search.get("enabled") and not graph_void:
            try:
                subgraph = self.deep_reflexion.memory.graph.get_entity_subgraph(focus_entity, max_hops=1)
                graph_void = not bool(subgraph.get("relations"))
            except (AttributeError, OSError, ValueError):
                graph_void = False
        if search.get("enabled") and (grounding < 0.60 or graph_void):
            try:
                dispatch = self.epistemic_dispatcher.enqueue_void(
                    query_text=request.goal, context={**safe_context, "focus_entity": focus_entity},
                    reason="low_evidence_grounding" if grounding < 0.60 else "missing_graph_relations")
                metadata["epistemic_discovery"] = {**dispatch, "evidence_grounding": grounding,
                    "graph_void_detected": graph_void, "non_blocking": True}
            except (OSError, ValueError, TypeError):
                metadata["epistemic_discovery"] = {"enqueued": False, "reason": "dispatch_error"}
        if search.get("enabled") and selected_score is not None and (selected_score < 0.45 or not search.get("selected_verified", False)):
            domain = selected_domain if isinstance(selected_domain, str) and selected_domain.strip() else "general_reference"
            reflection = self.deep_reflexion.reflect(goal=request.goal,
                failure="PRM trajectory fell below acceptance threshold or failed grounding/consistency checks",
                domain=domain, evidence=list(selected))
            metadata["deep_reflexion"] = reflection
        try:
            episode = self.deep_reflexion.memory.record_episode(
                goal=request.goal, domain=selected_domain if isinstance(selected_domain, str) else None,
                trace={"reasoning_mode": metadata["reasoning_mode"], "trajectory_count": search.get("trajectory_count", 0),
                       "selection_reason": search.get("selection_reason"), "input_evidence_count": len(evidence)},
                trajectory_scores=search.get("trajectories", []),
                chosen_path_index=search.get("selected_trajectory_id"),
                feedback={"selected_score": selected_score, "selected_verified": search.get("selected_verified", False)})
            metadata["episode_id"] = episode["id"]
        except (OSError, ValueError, TypeError):
            metadata["episode_persisted"] = False
        return IntelligenceResponse(answer=response.answer, evidence=response.evidence or selected, actions=response.actions,
                                    confidence=response.confidence, metadata=metadata)
