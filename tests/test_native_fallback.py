from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine
from universal_intelligence_engine.evaluation import BasicResponseEvaluator
from universal_intelligence_engine.providers import NoKnowledgeProvider
from universal_intelligence_engine.reasoning import NativeReasoningProvider


class EmptyLearningStore:
    def record(self, event):
        return None


def test_native_reasoning_fallback_is_explicit_and_does_not_invent_evidence():
    engine = UniversalIntelligenceEngine(
        knowledge=NoKnowledgeProvider(),
        reasoning=NativeReasoningProvider(),
        evaluator=BasicResponseEvaluator(),
        learning_store=EmptyLearningStore(),
    )
    result = engine.run(IntelligenceRequest(goal="What is established?", constraints={}))
    assert result.metadata["provider"] == "native"
    assert result.evidence == ()
    assert "cannot establish a supported answer" in result.answer.lower()


def test_require_sources_blocks_reasoning_when_evidence_is_empty():
    class MustNotRunReasoning:
        def reason(self, request, evidence):
            raise AssertionError("reasoning must not run without required evidence")

    engine = UniversalIntelligenceEngine(
        knowledge=NoKnowledgeProvider(),
        reasoning=MustNotRunReasoning(),
        evaluator=BasicResponseEvaluator(),
    )
    result = engine.run(IntelligenceRequest(goal="Evidence-bound question", constraints={"require_sources": True}))
    assert result.metadata["reason"] == "evidence_required_but_unavailable"
    assert result.evidence == ()
    assert result.confidence == 0.0
