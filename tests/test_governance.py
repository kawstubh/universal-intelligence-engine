import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from universal_intelligence_engine.governance import (
    ChangeProposal,
    GovernanceError,
    HumanAuthorizationGate,
)


def proposal():
    return ChangeProposal(
        change_id="CHANGE-000001",
        component="ARC",
        from_version="0.1.0",
        to_version="0.2.0",
        summary="Improve evidence selection",
        benchmark_results={"score": 0.92, "passed": True},
        security_results={"passed": True},
        regression_results={"passed": True},
    )


def test_gate_fails_closed_without_public_key(monkeypatch):
    monkeypatch.delenv("UIE_GOVERNANCE_PUBLIC_KEY", raising=False)
    with pytest.raises(GovernanceError):
        HumanAuthorizationGate()


def test_only_valid_human_signature_authorizes():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    public_bytes = public.public_bytes_raw()
    gate = HumanAuthorizationGate(base64.b64encode(public_bytes).decode())

    p = proposal()
    signature = base64.b64encode(private.sign(p.canonical_payload())).decode()

    result = gate.authorize(p, signature)

    assert result["status"] == "approved"
    assert result["change_id"] == p.change_id


def test_tampered_proposal_is_rejected():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    gate = HumanAuthorizationGate(
        base64.b64encode(public.public_bytes_raw()).decode()
    )

    p = proposal()
    signature = base64.b64encode(private.sign(p.canonical_payload())).decode()

    tampered = ChangeProposal(
        **{**p.__dict__, "summary": "Give AI unrestricted self-modification"}
    )

    with pytest.raises(GovernanceError):
        gate.authorize(tampered, signature)
