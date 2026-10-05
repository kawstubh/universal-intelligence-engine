"""scanO integration boundary.

This module intentionally does not implement a live scanO API client.
scanO's authenticated API contract, credentials, tenancy, webhook semantics,
and payload schema must be supplied by scanO before production integration.

It provides a stable internal boundary so the Dental/ UIE stack can consume
scanO-style screening results without coupling clinical logic to a vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ScanOSignal:
    condition: str
    risk: str
    confidence: float | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class ScanOResult:
    scan_id: str | None
    signals: tuple[ScanOSignal, ...]
    source: str = "scanO"
    raw_payload: Mapping[str, Any] | None = None


class ScanOAdapter:
    """Normalize a scanO-compatible response at the integration boundary.

    The adapter preserves the original payload for provenance and deliberately
    does not diagnose, reinterpret, or manufacture clinical findings.
    """

    def normalize(self, payload: Mapping[str, Any]) -> ScanOResult:
        scan_id = self._first_str(payload, "scan_id", "scanId", "id")
        raw_signals = payload.get("signals") or payload.get("conditions") or []

        signals: list[ScanOSignal] = []
        if isinstance(raw_signals, list):
            for item in raw_signals:
                if not isinstance(item, Mapping):
                    continue
                condition = self._first_str(item, "condition", "name", "label")
                risk = self._first_str(item, "risk", "risk_level", "severity")
                if not condition or not risk:
                    continue
                confidence = item.get("confidence")
                if isinstance(confidence, (int, float)):
                    confidence = max(0.0, min(1.0, float(confidence)))
                else:
                    confidence = None
                evidence = self._first_str(item, "evidence", "supporting_evidence")
                signals.append(
                    ScanOSignal(
                        condition=condition,
                        risk=risk,
                        confidence=confidence,
                        evidence=evidence,
                    )
                )

        return ScanOResult(
            scan_id=scan_id,
            signals=tuple(signals),
            raw_payload=payload,
        )

    @staticmethod
    def _first_str(payload: Mapping[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
