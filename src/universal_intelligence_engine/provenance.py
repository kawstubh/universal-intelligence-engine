"""Evidence provenance, freshness, and trust primitives."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ProvenanceRecord:
    source: str
    url: str | None
    retrieved_at: str
    published_at: str | None = None
    authority: float = 0.5
    corroboration: int = 0
    contradiction: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def freshness_hours(self) -> float | None:
        if not self.published_at:
            return None
        try:
            published = datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return max(0.0, (now - published).total_seconds() / 3600)
        except ValueError:
            return None

    def trust_score(self) -> float:
        corroboration_bonus = min(0.2, self.corroboration * 0.04)
        contradiction_penalty = min(0.35, self.contradiction * 0.08)
        return max(0.0, min(1.0, self.authority + corroboration_bonus - contradiction_penalty))
