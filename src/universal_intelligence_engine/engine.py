"""Orchestration kernel for the Universal Intelligence Engine."""

from .algorithm import AdaptiveReasoningCore
from .contracts import Evaluator, IntelligenceRequest, IntelligenceResponse, KnowledgeProvider, LearningStore, ReasoningProvider
from .knowledge import rank_evidence
from .reasoning import NativeReasoningProvider
from .runtime import SenseActRuntime


class UniversalIntelligenceEngine:
    """Run UIE's native cognitive-control algorithm around replaceable providers."""

    def __init__(self, knowledge: KnowledgeProvider, reasoning: ReasoningProvider | None = None,
                 evaluator: Evaluator | None = None, learning_store: LearningStore | None = None,
                 algorithm: AdaptiveReasoningCore | None = None, runtime: SenseActRuntime | None = None) -> None:
        self.knowledge = knowledge
        # Native reasoning is the fail-safe baseline; external providers are optional accelerators.
        self.reasoning = reasoning or NativeReasoningProvider()
        self.evaluator = evaluator
        self.learning_store = learning_store
        self.algorithm = algorithm or AdaptiveReasoningCore()
        self.runtime = runtime or SenseActRuntime()

    def observe_url(self, url: str):
        """Observe an external HTTP resource through UIE eyes."""
        return self.runtime.observe_url(url)

    def execute_http(self, **kwargs):
        """Execute an external HTTP action through UIE hands and policy gates."""
        return self.runtime.execute_http(**kwargs)

    def run(self, request: IntelligenceRequest) -> IntelligenceResponse:
        raw_evidence = tuple(self.knowledge.search(request))
        fused_evidence = tuple(rank_evidence(raw_evidence))
        decision = self.algorithm.decide(request, fused_evidence)
        evidence = decision.evidence
        if request.constraints.get("require_sources") and not evidence:
            response = IntelligenceResponse(
                answer=(
                    "I cannot establish a supported answer because no evidence "
                    "was retrieved. Configure a knowledge provider or retry later."
                ),
                evidence=(),
                confidence=0.0,
                metadata={
                    "provider": "none",
                    "reasoning_skipped": True,
                    "reason": "required_evidence_unavailable",
                },
            )
        else:
            response = self.reasoning.reason(request, evidence)
        evaluation = self.evaluator.evaluate(response) if self.evaluator else None

        if self.learning_store is not None:
            evaluation_map = dict(evaluation or {})
            reward = float(evaluation_map.get("score", 0.0) or 0.0)
            self.learning_store.record({
                "capability": request.context.get("capability", "intelligence"),
                "outcome": "passed" if evaluation_map.get("passed") else "completed",
                "reward": reward, "goal": request.goal, "locale": request.locale,
                "language": request.language, "evidence_count": len(evidence),
                "evaluation": evaluation_map, "algorithm": "ARC", "algorithm_version": "0.2.0",
                "decision": decision.action, "decision_confidence": decision.confidence,
                "decision_signals": decision.signals,
            })

        metadata = dict(response.metadata)
        metadata.update({
            "evidence_count": len(evidence),
            "raw_evidence_count": len(raw_evidence),
            "algorithm": "ARC",
            "algorithm_version": "0.2.0",
            "decision": decision.action,
            "decision_confidence": decision.confidence,
            "decision_signals": decision.signals,
            "decision_trace": list(decision.trace),
        })
        if evaluation is not None:
            metadata["evaluation"] = evaluation

        return IntelligenceResponse(
            answer=response.answer,
            evidence=response.evidence or evidence,
            actions=response.actions,
            confidence=response.confidence if response.confidence is not None else decision.confidence,
            metadata=metadata,
        )

    def run_autonomous(
        self,
        request: IntelligenceRequest,
        *,
        max_iterations: int = 3,
        target_confidence: float = 0.72,
    ) -> IntelligenceResponse:
        """Run a bounded observe→reason→verify→replan loop.

        The control layer remains deterministic and bounded: each iteration
        records observations, low-confidence results trigger another evidence
        pass, and the run stops at a confidence target or iteration budget.
        """
        budget = max(1, min(int(max_iterations), 8))
        target = max(0.0, min(float(target_confidence), 1.0))
        current = request
        trajectory: list[dict[str, object]] = []
        best: IntelligenceResponse | None = None

        for iteration in range(1, budget + 1):
            try:
                result = self.run(current)
            except Exception as exc:
                trajectory.append({
                    "iteration": iteration,
                    "status": "failed",
                    "error": str(exc),
                    "replanned": iteration < budget,
                })
                if best is not None:
                    metadata = dict(best.metadata)
                    metadata.update({
                        "autonomous": True,
                        "trajectory": trajectory,
                        "recovered": True,
                    })
                    return IntelligenceResponse(
                        best.answer, best.evidence, best.actions,
                        best.confidence, metadata,
                    )
                raise

            score = float(result.confidence or 0.0)
            evaluation = result.metadata.get("evaluation") or {}
            passed = bool(evaluation.get("passed", False))
            decision = str(result.metadata.get("decision", ""))
            best = result if best is None or score >= float(best.confidence or 0.0) else best
            should_stop = (
                score >= target
                and decision != "research"
                and (passed or self.evaluator is None)
            )
            trajectory.append({
                "iteration": iteration,
                "status": "completed",
                "confidence": score,
                "decision": decision,
                "evidence_count": len(result.evidence),
                "replanned": not should_stop and iteration < budget,
            })
            if should_stop or iteration >= budget:
                metadata = dict(best.metadata)
                metadata.update({
                    "autonomous": True,
                    "iterations": iteration,
                    "trajectory": trajectory,
                    "termination": "target_reached" if should_stop else "budget_exhausted",
                })
                return IntelligenceResponse(
                    best.answer, best.evidence, best.actions,
                    best.confidence, metadata,
                )

            prior = dict(current.context)
            prior.update({
                "autonomous_iteration": iteration,
                "previous_decision": decision,
                "previous_confidence": score,
                "previous_evidence_count": len(result.evidence),
                "replan_reason": "confidence_below_target_or_research_required",
            })
            current = IntelligenceRequest(
                goal=request.goal,
                context=prior,
                locale=request.locale,
                language=request.language,
                constraints=request.constraints,
            )

        return best or self.run(request)
