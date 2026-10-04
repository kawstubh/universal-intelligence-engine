"""Orchestration kernel for the Universal Intelligence Engine."""

from .algorithm import AdaptiveReasoningCore
from .contracts import Evaluator, IntelligenceRequest, IntelligenceResponse, KnowledgeProvider, LearningStore, ReasoningProvider
from .knowledge import rank_evidence


class UniversalIntelligenceEngine:
    """Run UIE's native cognitive-control algorithm around replaceable providers."""

    def __init__(self, knowledge: KnowledgeProvider, reasoning: ReasoningProvider,
                 evaluator: Evaluator | None = None, learning_store: LearningStore | None = None,
                 algorithm: AdaptiveReasoningCore | None = None) -> None:
        self.knowledge = knowledge
        self.reasoning = reasoning
        self.evaluator = evaluator
        self.learning_store = learning_store
        self.algorithm = algorithm or AdaptiveReasoningCore()

    def run(self, request: IntelligenceRequest) -> IntelligenceResponse:
        raw_evidence = tuple(self.knowledge.search(request))
        fused_evidence = tuple(rank_evidence(raw_evidence))
        decision = self.algorithm.decide(request, fused_evidence)
        evidence = decision.evidence
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
                "evaluation": evaluation_map, "algorithm": "ARC", "algorithm_version": "0.1.0",
                "decision": decision.action, "decision_confidence": decision.confidence,
                "decision_signals": decision.signals,
            })

        metadata = dict(response.metadata)
        metadata.update({
            "evidence_count": len(evidence),
            "raw_evidence_count": len(raw_evidence),
            "algorithm": "ARC",
            "algorithm_version": "0.1.0",
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
