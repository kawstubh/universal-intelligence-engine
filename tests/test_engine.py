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
