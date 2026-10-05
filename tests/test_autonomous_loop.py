from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine


class Knowledge:
    def search(self, request):
        return [
            Evidence(
                source="test",
                title="Authoritative result",
                retrieved_at="2026-10-05T00:00:00Z",
                content="The requested subject is supported.",
                url="https://example.com",
                confidence=0.95,
            )
        ]


class Reasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(
            answer="grounded answer",
            evidence=evidence,
            confidence=0.9,
        )


class Evaluator:
    def evaluate(self, response):
        return {"passed": True, "score": 0.9}


def test_autonomous_loop_stops_at_target():
    engine = UniversalIntelligenceEngine(
        knowledge=Knowledge(),
        reasoning=Reasoning(),
        evaluator=Evaluator(),
    )
    result = engine.run_autonomous(
        IntelligenceRequest(goal="What is the requested subject?"),
        max_iterations=3,
        target_confidence=0.72,
    )
    assert result.answer == "grounded answer"
    assert result.metadata["autonomous"] is True
    assert result.metadata["iterations"] == 1
    assert result.metadata["termination"] == "target_reached"
    assert result.metadata["trajectory"][0]["replanned"] is False


class LowConfidenceReasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(
            answer=f"attempt {request.context.get('autonomous_iteration', 0)}",
            evidence=evidence,
            confidence=0.4,
        )


def test_autonomous_loop_replans_until_budget():
    engine = UniversalIntelligenceEngine(
        knowledge=Knowledge(),
        reasoning=LowConfidenceReasoning(),
        evaluator=None,
    )
    result = engine.run_autonomous(
        IntelligenceRequest(goal="Investigate the subject"),
        max_iterations=3,
        target_confidence=0.72,
    )
    assert result.metadata["autonomous"] is True
    assert result.metadata["iterations"] == 3
    assert result.metadata["termination"] == "budget_exhausted"
    assert len(result.metadata["trajectory"]) == 3
    assert all(item["replanned"] for item in result.metadata["trajectory"][:-1])
