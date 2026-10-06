import asyncio
import pytest

from universal_intelligence_engine.scano_adapter import ScanOAdapter
from universal_intelligence_engine.whatsapp_adapter import WhatsAppAdapter


def test_scano_mock_mode(monkeypatch):
    monkeypatch.setenv("SCANO_MODE", "mock")
    result = asyncio.run(ScanOAdapter().screen({"scan_reference": "demo"}))
    assert result["status"] == "sandbox"
    assert result["provider"] == "mock"


def test_scano_requires_official_config(monkeypatch):
    monkeypatch.setenv("SCANO_MODE", "disabled")
    with pytest.raises(RuntimeError):
        asyncio.run(ScanOAdapter().screen({"scan_reference": "demo"}))


def test_whatsapp_requires_official_config(monkeypatch):
    monkeypatch.setenv("WHATSAPP_MODE", "disabled")
    with pytest.raises(RuntimeError):
        asyncio.run(WhatsAppAdapter().send_template("919999999999", "appointment_confirmation"))
