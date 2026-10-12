from universal_intelligence_engine import api


def test_health_reports_native_reasoning_fallback_when_openai_is_unconfigured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    providers = api.health()["providers"]

    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "native"
    assert providers["reasoning_model"] == "native"
    assert providers["knowledge"] is False
    assert providers["knowledge_provider"] == "unavailable"


def test_health_reports_configured_live_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-search-key")

    providers = api.health()["providers"]

    assert providers["reasoning"] is True
    assert providers["reasoning_provider"] == "openai-responses"
    assert providers["reasoning_model"] == "test-model"
    assert providers["knowledge"] is True
    assert providers["knowledge_provider"] == "brave-search"
