from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.evaluation import BasicResponseEvaluator


def test_basic_evaluator_accepts_grounded_response():
    response = IntelligenceResponse(
        answer="grounded",
        evidence=[
            Evidence(
                source="test",
                title="source",
                retrieved_at="2026-10-05T00:00:00Z",
                content="evidence",
            )
        ],
    )
    result = BasicResponseEvaluator().evaluate(response)
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_basic_evaluator_rejects_empty_response():
    result = BasicResponseEvaluator().evaluate(
        IntelligenceResponse(answer="", evidence=[])
    )
    assert result["passed"] is False


def test_request_contract_remains_provider_agnostic():
    request = IntelligenceRequest(
        goal="research",
        language="en",
        locale="IN-MH",
        constraints={"require_sources": True},
    )
    assert request.locale == "IN-MH"
