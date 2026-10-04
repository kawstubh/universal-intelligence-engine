"""Knowledge-provider helpers, evidence fusion, and deterministic ranking."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .contracts import Evidence
from .provenance import ProvenanceRecord


def _text_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _provenance(item: Evidence) -> ProvenanceRecord:
    raw = item.metadata.get("provenance", {}) if item.metadata else {}
    if isinstance(raw, ProvenanceRecord):
        return raw
    if isinstance(raw, dict):
        return ProvenanceRecord(
            source=str(raw.get("source") or item.source),
            url=raw.get("url") or item.url,
            retrieved_at=str(raw.get("retrieved_at") or item.retrieved_at),
            published_at=raw.get("published_at") or item.published_at,
            authority=float(raw.get("authority", 0.5) or 0.5),
            corroboration=int(raw.get("corroboration", 0) or 0),
            contradiction=int(raw.get("contradiction", 0) or 0),
        )
    return ProvenanceRecord(
        source=item.source,
        url=item.url,
        retrieved_at=item.retrieved_at,
        published_at=item.published_at,
        authority=0.5,
    )


def evidence_score(item: Evidence) -> float:
    """Return a conservative ranking score; ranking is not a truth judgment."""
    provenance = _provenance(item)
    trust = provenance.trust_score()
    freshness = provenance.freshness_hours
    freshness_score = 0.5 if freshness is None else max(0.0, min(1.0, 1.0 / (1.0 + freshness / 168.0)))
    explicit_confidence = 0.5 if item.confidence is None else max(0.0, min(1.0, float(item.confidence)))
    return round(
        (trust * 0.55) + (explicit_confidence * 0.30) + (freshness_score * 0.15),
        6,
    )


def deduplicate_evidence(evidence: Sequence[Evidence]) -> list[Evidence]:
    """Remove obvious duplicate records while preserving source diversity."""
    seen: set[tuple[str, str]] = set()
    result: list[Evidence] = []
    for item in evidence:
        identity = (
            item.url.strip() if item.url else _text_key(item.title),
            _text_key(item.content)[:240],
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def rank_evidence(evidence: Sequence[Evidence]) -> list[Evidence]:
    """Rank evidence using trust, explicit confidence, and freshness."""
    unique = deduplicate_evidence(evidence)
    return sorted(
        unique,
        key=lambda item: (evidence_score(item), len(item.content)),
        reverse=True,
    )
