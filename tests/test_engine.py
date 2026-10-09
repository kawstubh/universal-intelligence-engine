from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine


class Knowledge:
    def search(self, request):
        return [
            Evidence(
                source="test",
                title="Example",
                retrieved_at="2026-10-04T00:00:00Z",
                content="Evidence",
            )
        ]


class Reasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(answer={"goal": request.goal})


class Evaluator:
    def evaluate(self, response):
        return {"passed": True}


class Learning:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def test_engine_orchestrates():
    learning = Learning()
    engine = UniversalIntelligenceEngine(Knowledge(), Reasoning(), Evaluator(), learning)

    result = engine.run(IntelligenceRequest(goal="test"))

    assert result.answer["goal"] == "test"
    assert result.metadata["evaluation"]["passed"] is True
    assert learning.events[0]["evidence_count"] == 1


def test_require_sources_never_calls_reasoner_without_evidence():
    from universal_intelligence_engine.evaluation import BasicResponseEvaluator

    class EmptyKnowledge:
        def search(self, request):
            return []

    class MustNotReasonWithoutSources:
        def reason(self, request, evidence):
            raise AssertionError("reasoning must not run without required evidence")

    engine = UniversalIntelligenceEngine(
        EmptyKnowledge(),
        MustNotReasonWithoutSources(),
        BasicResponseEvaluator(),
    )
    result = engine.run(
        IntelligenceRequest(
            goal="Find a supported answer",
            constraints={"require_sources": True},
        )
    )

    assert result.metadata["reason"] == "required_evidence_unavailable"
    assert result.metadata["reasoning_skipped"] is True
    assert result.confidence == 0.0
    assert result.evidence == ()
    assert result.metadata["evaluation"]["passed"] is False
