"""Minimal orchestration kernel for the Universal Intelligence Engine."""

from .contracts import (
    Evaluator,
    IntelligenceRequest,
    IntelligenceResponse,
    KnowledgeProvider,
    LearningStore,
    ReasoningProvider,
)


class UniversalIntelligenceEngine:
    """Coordinate knowledge retrieval, reasoning, evaluation, and feedback."""

    def __init__(
        self,
        knowledge: KnowledgeProvider,
        reasoning: ReasoningProvider,
        evaluator: Evaluator | None = None,
        learning_store: LearningStore | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.reasoning = reasoning
        self.evaluator = evaluator
        self.learning_store = learning_store

    def run(self, request: IntelligenceRequest) -> IntelligenceResponse:
        evidence = tuple(self.knowledge.search(request))
        response = self.reasoning.reason(request, evidence)

        evaluation = self.evaluator.evaluate(response) if self.evaluator else None

        if self.learning_store is not None:
            self.learning_store.record(
                {
                    "goal": request.goal,
                    "locale": request.locale,
                    "language": request.language,
                    "evidence_count": len(evidence),
                    "evaluation": evaluation or {},
                }
            )

        if evaluation is None:
            return response

        metadata = dict(response.metadata)
        metadata["evaluation"] = evaluation

        return IntelligenceResponse(
            answer=response.answer,
            evidence=response.evidence or evidence,
            actions=response.actions,
            confidence=response.confidence,
            metadata=metadata,
        )
