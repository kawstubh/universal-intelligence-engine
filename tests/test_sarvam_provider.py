import pytest

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest
from universal_intelligence_engine import sarvam


def test_sarvam_provider_sends_authenticated_v2_chat_request(monkeypatch):
    captured = {}

    def fake_request(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {
            "id": "sarvam-test-id",
            "choices": [{"message": {"content": "Namaste from Sarvam"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(sarvam, "_json_request", fake_request)
    provider = sarvam.SarvamTokenFactoryReasoningProvider(api_key="test-secret")
    evidence = [Evidence(
        source="test-source",
        title="Test evidence",
        retrieved_at="2026-11-25T00:00:00Z",
        content="A test evidence statement.",
        url="https://example.org/evidence",
    )]
    response = provider.reason(
        IntelligenceRequest(goal="Explain this", language="en", locale="IN-MH"),
        evidence,
    )

    assert response.answer == "Namaste from Sarvam"
    assert response.metadata["provider"] == "sarvam-token-factory"
    assert captured["url"] == "https://api.sarvam.ai/v2/chat/completions"
    assert captured["headers"]["api-subscription-key"] == "test-secret"
    assert captured["payload"]["model"] == "sarvam-105b"
    assert "A test evidence statement." in captured["payload"]["messages"][1]["content"]


def test_sarvam_provider_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    provider = sarvam.SarvamTokenFactoryReasoningProvider()
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY is not configured"):
        provider.reason(IntelligenceRequest(goal="test"), [])


def test_health_reports_sarvam_without_exposing_key(monkeypatch):
    from universal_intelligence_engine import api

    monkeypatch.setenv("SARVAM_API_KEY", "do-not-return-this")
    monkeypatch.setenv("UIE_REASONING_PROVIDER", "sarvam")
    health = api.health()

    assert health["providers"]["reasoning_provider"] == "sarvam-token-factory"
    assert health["providers"]["reasoning_model"] == "sarvam-105b"
    assert health["providers"]["reasoning_configured"] is True
    assert "do-not-return-this" not in str(health)


def test_build_service_selects_sarvam_only_when_configured(monkeypatch):
    from universal_intelligence_engine import api
    from universal_intelligence_engine.sarvam import SarvamTokenFactoryReasoningProvider

    monkeypatch.setenv("SARVAM_API_KEY", "test-secret")
    monkeypatch.setenv("UIE_REASONING_PROVIDER", "sarvam")
    service = api.build_service()
    assert isinstance(service.engine.reasoning, SarvamTokenFactoryReasoningProvider)
