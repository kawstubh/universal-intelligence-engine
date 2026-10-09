from fastapi.testclient import TestClient

from universal_intelligence_engine import __version__
from universal_intelligence_engine import api
from universal_intelligence_engine.contracts import IntelligenceRequest, IntelligenceResponse


client = TestClient(api.app)


def test_health_uses_package_version_and_reports_provider_configuration(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == __version__
    assert payload["providers"] == {"knowledge": False, "reasoning": False}


def test_intelligence_fails_closed_when_api_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("UIE_API_KEY", raising=False)

    response = client.post(
        "/v1/intelligence/run",
        json={"goal": "test request"},
    )

    assert response.status_code == 503
    assert "fail-closed" in response.json()["detail"]


def test_intelligence_rejects_missing_or_invalid_bearer_token(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "test-secret-value")

    missing = client.post("/v1/intelligence/run", json={"goal": "test request"})
    invalid = client.post(
        "/v1/intelligence/run",
        headers={"Authorization": "Bearer wrong-value"},
        json={"goal": "test request"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert "test-secret-value" not in invalid.text


def test_intelligence_passes_locale_language_and_constraints_to_service(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "test-secret-value")
    captured = {}

    def fake_execute(request):
        captured["request"] = request
        return IntelligenceResponse(
            answer="A tested answer",
            evidence=[],
            actions=[],
            confidence=0.9,
            metadata={"test": True},
        )

    monkeypatch.setattr(api.service, "execute", fake_execute)
    response = client.post(
        "/v1/intelligence/run",
        headers={"Authorization": "Bearer test-secret-value"},
        json={
            "goal": "test request",
            "locale": "IN-MH",
            "language": "mr",
            "constraints": {"require_sources": True},
        },
    )

    assert response.status_code == 200
    assert captured["request"].locale == "IN-MH"
    assert captured["request"].language == "mr"
    assert captured["request"].constraints["require_sources"] is True
    assert response.json()["answer"] == "A tested answer"


def test_intelligence_rejects_invalid_request_before_execution(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "test-secret-value")

    response = client.post(
        "/v1/intelligence/run",
        headers={"Authorization": "Bearer test-secret-value"},
        json={"goal": ""},
    )

    assert response.status_code == 422


def test_service_uses_native_reasoning_and_empty_evidence_when_providers_are_unconfigured(
    monkeypatch, tmp_path
):
    from universal_intelligence_engine.providers import NoKnowledgeProvider
    from universal_intelligence_engine.reasoning import NativeReasoningProvider

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UIE_MEMORY_PATH", str(tmp_path / "learning.jsonl"))

    service = api.build_service()
    result = service.execute(IntelligenceRequest(goal="Explain a subject"))

    assert isinstance(service.engine.knowledge, NoKnowledgeProvider)
    assert isinstance(service.engine.reasoning, NativeReasoningProvider)
    assert result.evidence == ()
    assert "cannot establish a supported answer" in result.answer
    assert result.metadata["provider"] == "native"
