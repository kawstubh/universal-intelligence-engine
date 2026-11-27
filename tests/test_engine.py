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


class FailingReasoning:
    def reason(self, request, evidence):
        raise RuntimeError("simulated upstream provider outage")


def test_engine_falls_back_when_external_reasoning_provider_fails():
    engine = UniversalIntelligenceEngine(Knowledge(), FailingReasoning(), Evaluator(), Learning())

    result = engine.run(IntelligenceRequest(goal="test"))

    assert result.metadata["provider"] == "native-fallback"
    assert result.metadata["primary_provider"] == "FailingReasoning"
    assert result.metadata["fallback_used"] is True
    assert result.metadata["service_degraded"] is True
    assert "Evidence-supported findings" in result.answer
