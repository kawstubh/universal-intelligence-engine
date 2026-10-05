from universal_intelligence_engine.scano_adapter import ScanOAdapter


def test_scano_adapter_normalizes_public_style_signals():
    payload = {
        "scan_id": "demo-001",
        "signals": [
            {
                "condition": "caries",
                "risk": "high",
                "confidence": 0.94,
                "evidence": "example fixture",
            }
        ],
        "vendor_extra": {"ignored": True},
    }

    result = ScanOAdapter().normalize(payload)

    assert result.scan_id == "demo-001"
    assert result.source == "scanO"
    assert result.signals[0].condition == "caries"
    assert result.signals[0].confidence == 0.94
    assert result.raw_payload == payload


def test_scano_adapter_does_not_invent_missing_signals():
    result = ScanOAdapter().normalize({"scan_id": "empty", "signals": []})
    assert result.signals == ()
