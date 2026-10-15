"""Bounded, allowlist-constrained discovery goals from knowledge gaps."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import json
import warnings

from .query_claims import ClaimQueryService
from .semantic_matcher import SemanticMatcher

DEFAULT_SEEDS = Path(__file__).resolve().parents[1] / "config" / "seeds.json"


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)))[:8]


class CuriosityAgent:
    """Identify unresolved query topics and map them only to reviewed seed domains."""

    def __init__(self, claim_query: ClaimQueryService | None = None, seed_path: str | Path | None = None):
        self.claim_query = claim_query or ClaimQueryService()
        self.seed_path = Path(seed_path) if seed_path else DEFAULT_SEEDS

    def inspect(self, query: str, max_targets: int = 3) -> list[dict]:
        warnings.warn("CuriosityAgent.inspect() is deprecated; use await ainspect() for async storage.", DeprecationWarning, stacklevel=2)
        return self._inspect_claims(query, self.claim_query.query(limit=100)["claims"], max_targets)

    async def ainspect(self, query: str, max_targets: int = 3, *, storage=None) -> list[dict]:
        """Async-first curiosity scan; reads claim evidence via the shared async adapter."""
        result = await self.claim_query.aquery(limit=100, include_graph=False, storage=storage)
        return self._inspect_claims(query, result["claims"], max_targets)

    def _inspect_claims(self, query: str, claims: list[dict], max_targets: int) -> list[dict]:
        max_targets = max(1, min(int(max_targets), 3))
        words = _tokens(query)
        relevant = SemanticMatcher(threshold=0.18).match(query, claims, threshold=0.18, limit=100)
        if relevant and all(c["status"] not in {"conflicted_evidence", "unverified_claim", "superseded_temporal"} for c in relevant):
            return []
        seeds = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
        query_lower = query.lower()
        category_terms = {
            "health_life_sciences": ("health", "clinical", "medicine", "dose", "disease", "who", "patient"),
            "defence_geopolitics": ("defence", "defense", "military", "war", "geopolitic", "nato"),
            "politics_government_countries": ("government", "regulation", "law", "policy", "country", "india", "united nations"),
            "technology_science": ("technology", "software", "science", "specification", "standard", "w3c", "protocol", "ai"),
            "markets_finance_industry": ("economy", "economic", "market", "finance", "revenue", "inflation", "rbi", "stock"),
            "daily_needs_agriculture_environment": ("climate", "environment", "agriculture", "energy", "food", "weather"),
            "news_current_affairs": ("news", "current", "latest", "today", "election"),
        }
        matched = [cat for cat, terms in category_terms.items() if any(term in query_lower for term in terms)]
        categories = matched or ["technology_science", "politics_government_countries", "health_life_sciences"]
        current_year = datetime.now(timezone.utc).year
        entity = " ".join(words[:5]) or query[:100]
        targets = []
        for category in categories:
            for url in seeds.get(category, []):
                host = (urlparse(url).hostname or "").lower()
                if not host or not url.startswith("https://"):
                    continue
                targets.append({"topic": query[:300], "domain": category, "target_entity": entity, "temporal_focus": current_year, "seed_url": url, "target_host": host, "reason": "missing_or_low_confidence_or_conflicted_knowledge" if not relevant else "claim_requires_verification"})
                break
            if len(targets) >= max_targets:
                break
        return targets
