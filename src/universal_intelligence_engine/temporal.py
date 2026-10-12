"""Temporal and contradiction analysis for UIE evidence.

This module is deliberately conservative: it flags likely conflicts rather than
pretending that lexical similarity proves a factual contradiction.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from typing import Sequence

from .contracts import Evidence


@dataclass(frozen=True)
class EvidenceConflict:
    left_source: str
    right_source: str
    reason: str
    severity: float


@dataclass(frozen=True)
class TemporalAssessment:
    freshness: float
    age_hours: float | None
    stale: bool


_NEGATION = {"not", "no", "never", "without", "cannot", "can't", "failed", "rejected", "false", "denied"}
_POSITIVE = {"approved", "accepted", "successful", "effective", "works", "true", "increased", "higher"}
_NEGATIVE = {"rejected", "failed", "ineffective", "unsafe", "false", "decreased", "lower", "denied"}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()))


def _polarity(text: str) -> int:
    tokens = _tokens(text)
    positive = len(tokens & _POSITIVE)
    negative = len(tokens & _NEGATIVE) + len(tokens & _NEGATION)
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def temporal_assessment(item: Evidence, now: datetime | None = None,
                        stale_after_hours: float = 24 * 30) -> TemporalAssessment:
    """Compute a bounded freshness score; unknown dates remain explicitly unknown."""
    published = _parse_time(item.published_at)
    if published is None:
        return TemporalAssessment(freshness=0.45, age_hours=None, stale=False)
    current = now or datetime.now(timezone.utc)
    age_hours = max(0.0, (current - published).total_seconds() / 3600.0)
    # Half-life of 30 days; this is a ranking signal, not a truth score.
    freshness = math.exp(-age_hours / (24 * 30))
    return TemporalAssessment(
        freshness=round(max(0.0, min(1.0, freshness)), 4),
        age_hours=round(age_hours, 2),
        stale=age_hours > stale_after_hours,
    )


def detect_contradictions(evidence: Sequence[Evidence]) -> tuple[EvidenceConflict, ...]:
    """Flag likely conflicts where sources discuss a similar topic with opposing polarity."""
    conflicts: list[EvidenceConflict] = []
    for left, right in combinations(evidence, 2):
        left_tokens = _tokens(f"{left.title} {left.content}")
        right_tokens = _tokens(f"{right.title} {right.content}")
        overlap = len(left_tokens & right_tokens)
        denominator = max(1, min(len(left_tokens), len(right_tokens)))
        similarity = overlap / denominator
        if similarity < 0.35:
            continue
        lp, rp = _polarity(f"{left.title} {left.content}"), _polarity(f"{right.title} {right.content}")
        if lp == 0 or rp == 0 or lp == rp:
            continue
        severity = round(min(1.0, similarity), 4)
        conflicts.append(EvidenceConflict(
            left_source=left.source or "unknown",
            right_source=right.source or "unknown",
            reason="similar claims with opposing polarity markers",
            severity=severity,
        ))
    return tuple(conflicts)
