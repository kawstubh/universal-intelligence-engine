
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


def test_missing_knowledge_provider_fails_closed(monkeypatch):
    from universal_intelligence_engine.providers import BraveSearchProvider, ProviderNotConfiguredError
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    try:
        BraveSearchProvider().search(IntelligenceRequest(goal="test"))
        assert False, "expected provider configuration error"
    except ProviderNotConfiguredError as exc:
        assert exc.code == "PROVIDER_NOT_CONFIGURED"
        assert exc.provider == "knowledge"


def test_missing_reasoning_provider_fails_closed(monkeypatch):
    from universal_intelligence_engine.providers import OpenAIResponsesReasoningProvider, ProviderNotConfiguredError
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    try:
        OpenAIResponsesReasoningProvider().reason(IntelligenceRequest(goal="test"), [])
        assert False, "expected provider configuration error"
    except ProviderNotConfiguredError as exc:
        assert exc.code == "PROVIDER_NOT_CONFIGURED"
        assert exc.provider == "reasoning"


def test_gemini_provider_uses_openai_compatible_endpoint(monkeypatch):
    from universal_intelligence_engine import providers
    monkeypatch.setenv("REASONING_PROVIDER", "gemini")
    monkeypatch.setenv("REASONING_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    captured = {}

    def fake_request(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["payload"] = kwargs["payload"]
        return {"id": "gemini-test", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(providers, "_json_request", fake_request)
    result = providers.OpenAIResponsesReasoningProvider().reason(IntelligenceRequest(goal="test"), [])
    assert result.answer == "ok"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer test-gemini-key"
    assert captured["payload"]["model"] == "gemini-3.6-flash"


def test_groq_provider_uses_openai_compatible_endpoint(monkeypatch):
    from universal_intelligence_engine import providers
    monkeypatch.setenv("REASONING_PROVIDER", "groq")
    monkeypatch.setenv("REASONING_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    captured = {}

    def fake_request(url, **kwargs):
        captured["url"] = url
        return {"id": "groq-test", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(providers, "_json_request", fake_request)
    result = providers.OpenAIResponsesReasoningProvider().reason(IntelligenceRequest(goal="test"), [])
    assert result.answer == "ok"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_disabled_knowledge_provider_returns_empty_evidence():
    from universal_intelligence_engine.providers import DisabledKnowledgeProvider
    assert DisabledKnowledgeProvider().search(IntelligenceRequest(goal="test")) == []
