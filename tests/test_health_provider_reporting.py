from universal_intelligence_engine import api
from universal_intelligence_engine.reasoning import NativeReasoningProvider


def test_health_reports_free_public_knowledge_and_native_reasoning(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    providers = api.health()["providers"]

    assert providers["knowledge"] is True
    assert providers["knowledge_provider"] == "wikipedia+europe-pmc-public-api"
    assert providers["knowledge_mode"] == "best-effort-public-sources"
    assert "Not broad web search" in providers["knowledge_limitations"]
    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "native"
    assert providers["reasoning_model"] == "native"


def test_service_uses_native_reasoning_when_openai_key_is_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    service = api.build_service()

    assert isinstance(service.engine.reasoning, NativeReasoningProvider)


def test_health_reports_openai_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    providers = api.health()["providers"]

    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "openai-responses"
    assert providers["reasoning_model"] == "test-model"
