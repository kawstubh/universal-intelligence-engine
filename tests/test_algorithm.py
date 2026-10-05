from universal_intelligence_engine.algorithm import AdaptiveReasoningCore
from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest
from universal_intelligence_engine.temporal import detect_contradictions


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


def test_arc_flags_conflicting_claims():
    evidence = [
        Evidence(source="a", title="Treatment approved", retrieved_at="now",
                 content="The treatment was approved and effective.", confidence=.9,
                 url="https://example.com/a"),
        Evidence(source="b", title="Treatment rejected", retrieved_at="now",
                 content="The treatment was rejected and ineffective.", confidence=.9,
                 url="https://example.com/b"),
    ]
    conflicts = detect_contradictions(evidence)
    assert conflicts
    decision = AdaptiveReasoningCore().decide(
        IntelligenceRequest(goal="treatment approval"),
        evidence,
    )
    assert decision.signals["contradiction"] > 0
    assert decision.action == "answer_with_uncertainty"


def test_arc_exposes_temporal_signal():
    evidence = [
        Evidence(source="a", title="Recent dental study", retrieved_at="2026-10-05T00:00:00+00:00",
                 published_at="2026-10-04T00:00:00+00:00",
                 content="Recent dental implant research", confidence=.9,
                 url="https://example.com/a"),
    ]
    decision = AdaptiveReasoningCore().decide(
        IntelligenceRequest(goal="dental implant research"),
        evidence,
    )
    assert 0 < decision.signals["freshness"] <= 1
