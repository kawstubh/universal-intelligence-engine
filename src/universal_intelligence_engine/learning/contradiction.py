"""Persistent structured claim memory and conservative contradiction detection."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .distiller import DistilledClaim


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _independent_source(url_a: str, url_b: str) -> bool:
    from urllib.parse import urlparse
    a = (urlparse(url_a).hostname or "").lower().removeprefix("www.")
    b = (urlparse(url_b).hostname or "").lower().removeprefix("www.")
    return bool(a and b and a != b)


def _is_negated(text: str) -> bool:
    return bool(re.search(r"\b(not|no|never|neither|without|cannot|can't|doesn't|isn't|aren't|wasn't|weren't|didn't|failed to|unable to)\b", text.lower()))


def _conflicts(a: DistilledClaim | dict, b: DistilledClaim) -> bool:
    """Detect clear polarity conflicts on matching subject/predicate only."""
    get = (lambda obj, key: getattr(obj, key) if hasattr(obj, key) else obj[key])
    if _norm(get(a, "subject")) != _norm(b.subject) or _norm(get(a, "predicate")) != _norm(b.predicate):
        return False
    old_obj, new_obj = get(a, "object_claim"), b.object_claim
    if _norm(old_obj) == _norm(new_obj):
        return False
    # Only assert a contradiction for a direct negation/polarity flip; avoid guessing
    # that two different numeric values or unrelated phrases necessarily conflict.
    old_neg, new_neg = _is_negated(old_obj), _is_negated(new_obj)
    if old_neg != new_neg:
        old_core = _norm(re.sub(r"\b(not|no|never|neither|without|cannot|can't|doesn't|isn't|aren't|wasn't|weren't|didn't|failed to|unable to)\b", "", old_obj))
        new_core = _norm(re.sub(r"\b(not|no|never|neither|without|cannot|can't|doesn't|isn't|aren't|wasn't|weren't|didn't|failed to|unable to)\b", "", new_obj))
        return old_core == new_core or old_core in new_core or new_core in old_core
    return False


class ContradictionEngine:
    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3", established_confidence: float = 0.80):
        self.db_path = str(db_path)
        self.established_confidence = max(0.0, min(1.0, float(established_confidence)))
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL,
                    object_claim TEXT NOT NULL, temporal_anchor TEXT, confidence REAL NOT NULL,
                    provenance_url TEXT NOT NULL, domain TEXT NOT NULL, status TEXT NOT NULL,
                    provenance_urls TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_claim_subject_predicate ON claims(subject,predicate);
                CREATE TABLE IF NOT EXISTS contradiction_log (
                    id TEXT PRIMARY KEY, existing_claim_id TEXT NOT NULL, incoming_claim_id TEXT NOT NULL,
                    existing_claim_json TEXT NOT NULL, incoming_claim_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'unresolved'
                );
            """)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def process_claim(self, claim: DistilledClaim) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        incoming_id = str(uuid.uuid4())
        payload = claim.to_dict()
        payload["status"] = "unverified_claim"
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM claims WHERE lower(subject)=lower(?) AND lower(predicate)=lower(?) ORDER BY confidence DESC", (claim.subject, claim.predicate)).fetchall()
            # A claim is independent evidence only when its registrable source host differs.
            for row in rows:
                prior = dict(row)
                prior["provenance_urls"] = json.loads(prior["provenance_urls"])
                if _norm(prior["object_claim"]) == _norm(claim.object_claim):
                    urls = list(dict.fromkeys(prior["provenance_urls"] + [claim.provenance_url]))
                    independent = any(_independent_source(url, claim.provenance_url) for url in prior["provenance_urls"])
                    reinforced = independent and claim.provenance_url not in prior["provenance_urls"]
                    confidence = min(0.95, prior["confidence"] + (0.05 if reinforced else 0.0))
                    status = prior["status"]
                    conn.execute("UPDATE claims SET confidence=?,provenance_urls=?,updated_at=? WHERE id=?", (confidence, json.dumps(urls), now, prior["id"]))
                    return {"id": prior["id"], "status": status, "action": "corroborated" if reinforced else "duplicate_source", "confidence": confidence, "provenance_urls": urls}
                if prior["confidence"] >= self.established_confidence and _conflicts(prior, claim):
                    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incoming_id, claim.subject, claim.predicate, claim.object_claim, claim.temporal_anchor, claim.confidence, claim.provenance_url, claim.domain, "conflicted_evidence", json.dumps([claim.provenance_url]), now, now))
                    incoming_json = {**payload, "id": incoming_id, "status": "conflicted_evidence"}
                    conn.execute("INSERT INTO contradiction_log(id,existing_claim_id,incoming_claim_id,existing_claim_json,incoming_claim_json,created_at) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), prior["id"], incoming_id, json.dumps(prior), json.dumps(incoming_json), now))
                    conn.execute("UPDATE claims SET status='conflicted_evidence',updated_at=? WHERE id=?", (now, prior["id"]))
                    return {"id": incoming_id, "status": "conflicted_evidence", "action": "contradiction_logged", "conflicts_with": prior["id"]}
            conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incoming_id, claim.subject, claim.predicate, claim.object_claim, claim.temporal_anchor, claim.confidence, claim.provenance_url, claim.domain, "unverified_claim", json.dumps([claim.provenance_url]), now, now))
        return {"id": incoming_id, "status": "unverified_claim", "action": "inserted", "confidence": claim.confidence}

    def process_claims(self, claims: Iterable[DistilledClaim]) -> list[dict]:
        return [self.process_claim(claim) for claim in claims]

    def get_claims(self, subject: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if subject:
                rows = conn.execute("SELECT * FROM claims WHERE lower(subject)=lower(?) ORDER BY created_at", (subject,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM claims ORDER BY created_at").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provenance_urls"] = json.loads(item["provenance_urls"])
            result.append(item)
        return result

    def contradictions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM contradiction_log ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
