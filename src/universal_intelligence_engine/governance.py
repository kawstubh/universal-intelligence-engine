"""Human-authorized governance gate for self-improvement.

The engine may propose changes, but it cannot authorize or activate them.
Activation requires an Ed25519 signature produced outside the engine and
verifiable with the configured UIE_GOVERNANCE_PUBLIC_KEY.

The governance key is intentionally not generated or stored by this package.
The private signing key must remain under the human operator's control.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class GovernanceError(RuntimeError):
    """Raised when a proposed self-improvement is not authorized."""


@dataclass(frozen=True)
class ChangeProposal:
    change_id: str
    component: str
    from_version: str
    to_version: str
    summary: str
    benchmark_results: dict[str, Any]
    security_results: dict[str, Any]
    regression_results: dict[str, Any]

    def canonical_payload(self) -> bytes:
        payload = {
            "change_id": self.change_id,
            "component": self.component,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "summary": self.summary,
            "benchmark_results": self.benchmark_results,
            "security_results": self.security_results,
            "regression_results": self.regression_results,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


class HumanAuthorizationGate:
    """Fail-closed gate for activating AI-generated changes."""

    def __init__(self, public_key: str | None = None) -> None:
        self.public_key = public_key or os.getenv("UIE_GOVERNANCE_PUBLIC_KEY")
        if not self.public_key:
            raise GovernanceError(
                "UIE_GOVERNANCE_PUBLIC_KEY is not configured; "
                "self-improvement activation is disabled."
            )
        try:
            self._verifier = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(self.public_key, validate=True)
            )
        except Exception as exc:
            raise GovernanceError("Invalid UIE governance public key.") from exc

    def verify(self, proposal: ChangeProposal, signature: str) -> bool:
        try:
            self._verifier.verify(
                base64.b64decode(signature, validate=True),
                proposal.canonical_payload(),
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    def authorize(self, proposal: ChangeProposal, signature: str) -> dict[str, Any]:
        if not self.verify(proposal, signature):
            raise GovernanceError(
                f"Human authorization failed for change {proposal.change_id}."
            )

        return {
            "status": "approved",
            "change_id": proposal.change_id,
            "component": proposal.component,
            "from_version": proposal.from_version,
            "to_version": proposal.to_version,
            "authorized_by": "human-governance-key",
            "audit_required": True,
        }
