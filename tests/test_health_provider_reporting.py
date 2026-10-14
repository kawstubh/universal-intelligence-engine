from universal_intelligence_engine import api


def test_health_reports_free_public_knowledge_and_native_reasoning_when_unconfigured(
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    providers = api.health()["providers"]

    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "native"
    assert providers["reasoning_model"] == "native"
    assert providers["knowledge"] is True
    assert providers["knowledge_provider"] == "wikipedia+europe-pmc-public-api"
    assert providers["knowledge_mode"] == "best-effort-public-sources"
    assert "Not broad web search" in providers["knowledge_limitations"]


def test_health_does_not_report_unused_openai_model_for_native_reasoning(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "configured-but-unused-model")

    providers = api.health()["providers"]

    assert providers["reasoning_provider"] == "native"
    assert providers["reasoning_model"] == "native"


def test_health_reports_configured_reasoning_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    providers = api.health()["providers"]

    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "openai-responses"
    assert providers["reasoning_model"] == "test-model"
    assert providers["knowledge"] is True
    assert providers["knowledge_provider"] == "wikipedia+europe-pmc-public-api"
