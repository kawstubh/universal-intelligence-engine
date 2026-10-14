"""Rule-based atomic claim extraction from public web documents.

Claims are proposals only. This module deliberately does not verify facts or train model weights.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


@dataclass(frozen=True)
class DistilledClaim:
    subject: str
    predicate: str
    object_claim: str
    temporal_anchor: str | None
    confidence: float
    provenance_url: str
    domain: str
    status: str = "unverified_claim"

    def to_dict(self) -> dict:
        return asdict(self)


# Extraction confidence is a bounded source-prior, not truth probability.
DOMAIN_AUTHORITY_PRIORS = {
    "who.int": 0.82, "cdc.gov": 0.82, "nih.gov": 0.82, "ncbi.nlm.nih.gov": 0.82,
    "europepmc.org": 0.80, "un.org": 0.80, "worldbank.org": 0.80,
    "sec.gov": 0.84, "rbi.org.in": 0.82, "arxiv.org": 0.74,
    "nature.com": 0.78, "science.org": 0.78, "reuters.com": 0.68,
    "apnews.com": 0.68, "bbc.com": 0.66, "thehindu.com": 0.64,
}

DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4}|(?:in|as of|since|during)\s+\d{4})\b")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
COPULA_RE = re.compile(r"^(?P<subject>[A-Z][\w’'&(). -]{1,100}?)\s+(?P<predicate>is|are|was|were|has|have|had|contains|contained|increased|decreased|reached|reported|announced|approved|banned|equals|equaled|causes|caused|produces|produced|requires|required|includes|included)\s+(?P<object>.+)$", re.IGNORECASE)


def _domain_host(url: str) -> str:
    return (urlparse(url).hostname or "unknown").lower().rstrip(".")


def _authority_prior(host: str) -> float:
    matches = [(domain, score) for domain, score in DOMAIN_AUTHORITY_PRIORS.items() if host == domain or host.endswith("." + domain)]
    return max(matches, key=lambda pair: len(pair[0]))[1] if matches else 0.55


def distill_claims(content: str, provenance_url: str, domain: str | None = None, max_claims: int = 30) -> list[DistilledClaim]:
    """Extract conservative sentence-level claims with explicit source provenance."""
    if not isinstance(content, str) or not content.strip() or not provenance_url:
        return []
    host = _domain_host(provenance_url)
    category = domain or host
    prior = _authority_prior(host)
    claims: list[DistilledClaim] = []
    seen: set[str] = set()
    for sentence in SENTENCE_RE.split(content[:80_000]):
        clean = re.sub(r"\s+", " ", sentence).strip(" \t\r\n•-")
        if len(clean) < 25 or len(clean) > 600:
            continue
        match = COPULA_RE.match(clean)
        if not match:
            continue
        subject = match.group("subject").strip(" ,:;.")
        predicate = match.group("predicate").lower()
        object_claim = match.group("object").strip(" \t\r\n")
        object_claim = re.sub(r"\s*\[[^]]{1,30}\]", "", object_claim).strip()
        if len(subject) < 2 or len(object_claim) < 5 or subject.lower() in {"this", "that", "it", "there", "they"}:
            continue
        key = f"{subject.lower()}|{predicate}|{object_claim.lower()}"
        if key in seen:
            continue
        seen.add(key)
        date_match = DATE_RE.search(clean)
        # Slightly reduce extraction confidence for long/compound sentences.
        confidence = max(0.30, min(0.90, prior - (0.05 if len(clean) > 250 else 0.0)))
        claims.append(DistilledClaim(subject, predicate, object_claim, date_match.group(0) if date_match else None,
                                     confidence, provenance_url, category, "unverified_claim"))
        if len(claims) >= max(1, min(int(max_claims), 100)):
            break
    return claims


class ClaimDistiller:
    def distill(self, content: str, provenance_url: str, domain: str | None = None, max_claims: int = 30) -> list[DistilledClaim]:
        return distill_claims(content, provenance_url, domain, max_claims)
