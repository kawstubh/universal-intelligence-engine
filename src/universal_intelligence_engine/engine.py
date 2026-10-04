"""Orchestration kernel for the Universal Intelligence Engine."""

from dataclasses import replace

from .contracts import (
    Evaluator,
    IntelligenceRequest,
    IntelligenceResponse,
    KnowledgeProvider,
    LearningStore,
    ReasoningProvider,
)
from .knowledge import rank_evidence
from .policy import CapabilityPolicy


class UniversalIntelligenceEngine:
    """Coordinate retrieval, evidence fusion, reasoning, evaluation, feedback, and policy."""

    def __init__(
        self,
        knowledge: KnowledgeProvider,
        reasoning: ReasoningProvider,
        evaluator: Evaluator | None = None,
        learning_store: LearningStore | None = None,
        policy: CapabilityPolicy | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.reasoning = reasoning
        self.evaluator = evaluator
        self.learning_store = learning_store
        self.policy = policy

    def run(self, request: IntelligenceRequest) -> IntelligenceResponse:
        safe_request = request
        if self.policy is not None:
            safe_request = replace(request, context=self.policy.filter_context(request.context))

        raw_evidence = tuple(self.knowledge.search(safe_request))
        evidence = tuple(rank_evidence(raw_evidence))
        response = self.reasoning.reason(safe_request, evidence)

        actions = tuple(response.actions)
        if self.policy is not None:
            actions = tuple(
                {
                    **action,
                    "requires_human_approval": self.policy.needs_approval(
                        str(action.get("action", ""))
                    ),
                }
                for action in actions
                if self.policy.allows(str(action.get("action", "")))
            )

        evaluation = self.evaluator.evaluate(response) if self.evaluator else None

        if self.learning_store is not None:
            evaluation_map = dict(evaluation or {})
            reward = float(evaluation_map.get("score", 0.0) or 0.0)
            self.learning_store.record(
                {
                    "capability": safe_request.context.get("capability", "intelligence"),
                    "outcome": "passed" if evaluation_map.get("passed") else "completed",
                    "reward": reward,
                    "goal": safe_request.goal,
                    "locale": safe_request.locale,
                    "language": safe_request.language,
                    "evidence_count": len(evidence),
                    "evaluation": evaluation_map,
                }
            )

        metadata = dict(response.metadata)
        metadata["evidence_count"] = len(evidence)
        metadata["raw_evidence_count"] = len(raw_evidence)
        metadata["policy_applied"] = self.policy is not None
        if self.policy is not None:
            metadata["context_domains"] = sorted(safe_request.context)
        if evaluation is not None:
            metadata["evaluation"] = evaluation

        return IntelligenceResponse(
            answer=response.answer,
            evidence=response.evidence or evidence,
            actions=actions,
            confidence=response.confidence,
            metadata=metadata,
        )
