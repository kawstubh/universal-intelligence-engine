from universal_intelligence_engine.algorithm import AdaptiveReasoningCore
from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest

def test_arc_selects_relevant_evidence():
    core = AdaptiveReasoningCore()
    request = IntelligenceRequest(goal="dental implant research")
    evidence = [
        Evidence(source="a", title="Dental implant study", retrieved_at="now",
                  content="Clinical research on dental implant outcomes", confidence=.9,
                  url="https://example.com/a"),
        Evidence(source="b", title="Weather", retrieved_at="now",
                  content="Rain tomorrow", confidence=.9, url="https://example.com/b"),
    ]
    decision = core.decide(request, evidence)
    assert decision.evidence[0].title == "Dental implant study"
    assert decision.confidence > 0
    assert decision.action in {"answer", "answer_with_uncertainty", "research"}
