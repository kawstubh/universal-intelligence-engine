import pytest

from universal_intelligence_engine.contracts import (
    Evidence,
    IntelligenceRequest,
    IntelligenceResponse,
)
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
    from universal_intelligence_engine.providers import (
        BraveSearchProvider,
        ProviderNotConfiguredError,
    )

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    try:
        BraveSearchProvider().search(IntelligenceRequest(goal="test"))
        assert False, "expected provider configuration error"
    except ProviderNotConfiguredError as exc:
        assert exc.code == "PROVIDER_NOT_CONFIGURED"
        assert exc.provider == "knowledge"


def test_tavily_provider_maps_search_results_to_evidence(monkeypatch):
    from universal_intelligence_engine import providers

    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    captured = {}

    class FakeTavilyClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key

        def search(self, **kwargs):
            captured["search_args"] = kwargs
            return {
                "results": [
                    {
                        "title": "Dental evidence",
                        "url": "https://example.org/dental",
                        "content": "Evidence summary",
                        "score": 0.91,
                    },
                    {
                        "title": "Invalid result",
                        "url": "javascript:alert(1)",
                        "content": "Should be ignored",
                    },
                ]
            }

    monkeypatch.setattr(providers, "TavilyClient", FakeTavilyClient)
    evidence = providers.TavilySearchProvider().search(
        IntelligenceRequest(goal="dental evidence", locale="IN-MH", language="en")
    )

    assert captured["api_key"] == "test-tavily-key"
    assert captured["search_args"]["query"] == "dental evidence region:IN-MH"
    assert captured["search_args"]["search_depth"] == "basic"
    assert captured["search_args"]["max_results"] == 8
    assert len(evidence) == 1
    assert evidence[0].source == "tavily-web"
    assert evidence[0].url == "https://example.org/dental"
    assert evidence[0].confidence == 0.91
    assert evidence[0].metadata["provider"] == "tavily"


def test_tavily_provider_uses_keyless_mode_without_key(monkeypatch):
    from universal_intelligence_engine import providers

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeTavilyClient:
        def __init__(self, *args, **kwargs):
            captured["constructed_without_key"] = not args and not kwargs

        def search(self, **kwargs):
            return {"results": []}

    monkeypatch.setattr(providers, "TavilyClient", FakeTavilyClient)
    evidence = providers.TavilySearchProvider().search(
        IntelligenceRequest(goal="keyless test")
    )

    assert evidence == []
    assert captured["constructed_without_key"] is True


def test_missing_reasoning_provider_fails_closed(monkeypatch):
    from universal_intelligence_engine.providers import (
        OpenAIResponsesReasoningProvider,
        ProviderNotConfiguredError,
    )

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
        return {
            "id": "gemini-test",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }

    monkeypatch.setattr(providers, "_json_request", fake_request)
    result = providers.OpenAIResponsesReasoningProvider().reason(
        IntelligenceRequest(goal="test"), []
    )
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
        return {
            "id": "groq-test",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }

    monkeypatch.setattr(providers, "_json_request", fake_request)
    result = providers.OpenAIResponsesReasoningProvider().reason(
        IntelligenceRequest(goal="test"), []
    )
    assert result.answer == "ok"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_disabled_knowledge_provider_returns_empty_evidence():
    from universal_intelligence_engine.providers import DisabledKnowledgeProvider

    assert DisabledKnowledgeProvider().search(IntelligenceRequest(goal="test")) == []


def test_gemini_falls_back_to_groq(monkeypatch):
    from universal_intelligence_engine import providers

    monkeypatch.setenv("REASONING_PROVIDER", "gemini")
    monkeypatch.setenv("REASONING_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("REASONING_FALLBACK_PROVIDER", "groq")
    monkeypatch.setenv("REASONING_FALLBACK_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        if "generativelanguage" in url:
            raise providers.ProviderUnavailable("primary unavailable")
        return {
            "id": "fallback",
            "choices": [{"message": {"content": "fallback ok"}}],
            "usage": {},
        }

    monkeypatch.setattr(providers, "_json_request", fake_request)
    result = providers.OpenAIResponsesReasoningProvider().reason(
        IntelligenceRequest(goal="test"), []
    )
    assert result.answer == "fallback ok"
    assert calls == [
        (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "Bearer gemini-key",
        ),
        ("https://api.groq.com/openai/v1/chat/completions", "Bearer groq-key"),
    ]


@pytest.mark.parametrize(
    "failure", ["timeout", "5xx", "429", "malformed-json", "non-json", "unexpected"]
)
def test_provider_failure_returns_structured_native_fallback(monkeypatch, failure):
    from universal_intelligence_engine import providers

    monkeypatch.setenv("REASONING_PROVIDER", "openai")
    monkeypatch.setenv("REASONING_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-" + "key")
    monkeypatch.delenv("REASONING_FALLBACK_PROVIDER", raising=False)

    def broken(*args, **kwargs):
        if failure == "timeout":
            raise providers.ProviderTimeout("timeout")
        if failure == "5xx":
            raise providers.ProviderUnavailable("unavailable")
        if failure == "429":
            raise providers.ProviderRateLimited("limited")
        if failure in ("malformed-json", "non-json"):
            raise providers.ProviderBadOutput("bad output")
        raise ValueError("unexpected private payload")

    monkeypatch.setattr(providers, "_json_request", broken)
    response = providers.OpenAIResponsesReasoningProvider().reason(
        IntelligenceRequest(goal="fallback test"), []
    )
    assert isinstance(response, IntelligenceResponse)
    assert isinstance(response.answer, str) and response.answer
    assert response.metadata["provider_used"] == "native-fallback"


def test_provider_failure_logs_do_not_leak_canary_data(monkeypatch, caplog):
    import logging

    from universal_intelligence_engine import providers

    monkeypatch.setenv("REASONING_PROVIDER", "openai")
    monkeypatch.setenv("REASONING_MODEL", "test-model")
    canary_key = "CANARY_" + "API_KEY_" + "49382"
    monkeypatch.setenv("OPENAI_API_KEY", canary_key)
    monkeypatch.delenv("REASONING_FALLBACK_PROVIDER", raising=False)
    request = IntelligenceRequest(
        goal="Patient CANARY_PATIENT_Mira",
        context={"phone": "CANARY_PHONE_9876543210", "note": "CANARY_NOTE_private"},
    )

    def broken(*args, **kwargs):
        raise ValueError(
            "CANARY_PATIENT_Mira CANARY_PHONE_9876543210 CANARY_NOTE_private "
            + canary_key
        )

    monkeypatch.setattr(providers, "_json_request", broken)
    with caplog.at_level(logging.WARNING):
        response = providers.OpenAIResponsesReasoningProvider().reason(request, [])
    assert response.metadata["provider_used"] == "native-fallback"
    for value in (
        "CANARY_PATIENT_Mira",
        "CANARY_PHONE_9876543210",
        "CANARY_NOTE_private",
        canary_key,
    ):
        assert value not in caplog.text


def test_timezone_z_normalization_preserves_timestamp_instant():
    timestamp = "2026-10-12T18:30:00Z"
    from universal_intelligence_engine.temporal import _parse_time

    before = _parse_time(timestamp.replace("Z", "+00:00"))
    after = _parse_time(timestamp)
    assert before == after
    assert before.timestamp() == after.timestamp()


def test_native_provider_failure_returns_static_fallback_without_log_leakage(
    monkeypatch, caplog
):
    import logging

    from universal_intelligence_engine import providers

    monkeypatch.setenv("REASONING_PROVIDER", "openai")
    monkeypatch.setenv("REASONING_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("REASONING_FALLBACK_PROVIDER", raising=False)

    def broken_provider(*args, **kwargs):
        raise ValueError("CANARY_NATIVE_MESSAGE_7721")

    monkeypatch.setattr(providers, "_json_request", broken_provider)
    monkeypatch.setattr(providers.NativeReasoningProvider, "reason", broken_provider)
    caplog.set_level(logging.WARNING)
    response = providers.OpenAIResponsesReasoningProvider().reason(
        IntelligenceRequest(goal="fallback test"), []
    )

    assert response.answer == "AI unavailable"
    assert response.evidence == []
    assert response.confidence == 0
    assert response.metadata["provider_used"] == "static-fallback"
    assert "CANARY_NATIVE_MESSAGE_7721" not in caplog.text


def test_api_provider_exception_message_is_not_logged(monkeypatch, caplog):
    import logging

    from fastapi.testclient import TestClient

    from universal_intelligence_engine import api

    canaries = (
        "CANARY_API_PATIENT_1842",
        "CANARY_API_PHONE_5550109",
        "CANARY_API_SECRET_773",
    )
    api_key = "test-" + "api-" + "key"
    monkeypatch.setenv("UIE_API_KEY", api_key)

    def fail_with_sensitive_message(*args, **kwargs):
        raise RuntimeError(" | ".join(canaries))

    monkeypatch.setattr(api.service, "execute", fail_with_sensitive_message)
    caplog.set_level(logging.DEBUG)
    with TestClient(api.app) as client:
        response = client.post(
            "/v1/intelligence/run",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"goal": "test", "context": {}, "constraints": {}},
        )

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "Provider request failed."
    for canary in canaries:
        assert canary not in caplog.text
