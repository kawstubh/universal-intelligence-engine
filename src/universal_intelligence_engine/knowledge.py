"""Knowledge-provider helpers and deterministic evidence ranking."""

from collections.abc import Sequence
from .contracts import Evidence


def rank_evidence(evidence: Sequence[Evidence]) -> list[Evidence]:
    """Rank evidence without pretending that ranking equals truth."""
    return sorted(
        evidence,
        key=lambda item: (
            item.confidence if item.confidence is not None else 0.5,
            len(item.content),
        ),
        reverse=True,
    )
