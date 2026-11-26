from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from universal_intelligence_engine import sarvam_adapter


def test_redaction_removes_identifiers_recursively():
    source = {
        "patient_id": "p-123",
        "patient": {"name": "Test Patient", "age": 40, "phone": "123"},
        "chart": [{"tooth_fdi": "36", "status": "caries", "id": "row-1"}],
    }
    safe = sarvam_adapter.redact_direct_identifiers(source)
    assert "patient_id" not in safe
    assert safe["patient"] == {"age": 40}
    assert safe["chart"] == [{"tooth_fdi": "36", "status": "caries"}]


@pytest.mark.asyncio
async def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        await sarvam_adapter.generate_sarvam_completion("patient_summary", "Summarize", {})
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "SARVAM_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_success_uses_server_key_and_redacted_context(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-key")
    monkeypatch.setenv("DENTAL_SARVAM_MODEL", "sarvam-105b")
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "Clinician review summary"}}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "payload": json})
            return FakeResponse()

    monkeypatch.setattr(sarvam_adapter.httpx, "AsyncClient", FakeClient)
    answer = await sarvam_adapter.generate_sarvam_completion(
        "patient_summary",
        "Summarize only supported facts",
        {"patient_id": "p-1", "patient": {"name": "Hidden", "age": 40}},
    )
    assert answer == "Clinician review summary"
    assert captured["headers"]["api-subscription-key"] == "test-only-key"
    prompt = captured["payload"]["messages"][1]["content"]
    assert "Hidden" not in prompt
    assert "p-1" not in prompt
    assert '"age": 40' in prompt


@pytest.mark.asyncio
async def test_provider_auth_failure_is_sanitized(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-key")

    class FakeResponse:
        status_code = 401
    class FakeClient:
        def __init__(self, timeout): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(sarvam_adapter.httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as error:
        await sarvam_adapter.generate_sarvam_completion("patient_summary", "Summarize", {})
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "SARVAM_PROVIDER_ERROR"
