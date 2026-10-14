"""Persistent structured claim memory with conservative contradiction detection."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .distiller import DistilledClaim


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _independent_source(url_a: str, url_b: str) -> bool:
    a = (urlparse(url_a).hostname or "").lower().removeprefix("www.")
    b = (urlparse(url_b).hostname or "").lower().removeprefix("www.")
    return bool(a and b and a != b)


def _is_negated(text: str) -> bool:
    return bool(re.search(r"\b(not|no|never|neither|without|cannot|can't|doesn't|isn't|aren't|wasn't|weren't|didn't|failed to|unable to)\b", text.lower()))


_NUMERIC_RE = re.compile(r"(?P<currency>\$|€|£|₹)?\s*(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>thousand|million|billion|k|m|bn|b)?\s*(?P<unit>%|percent|mg|mcg|µg|μg|g|kg|ml|mL|l|L|mmol|mol|km|miles?|km²|m2|employees?|offices?|users?|people|units?)?", re.IGNORECASE)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_UNIT = {"ml": "ml", "mg": "mg", "mcg": "mcg", "µg": "mcg", "μg": "mcg", "l": "l", "g": "g", "kg": "kg", "percent": "%", "%": "%", "employees": "employee", "employee": "employee", "offices": "office", "office": "office", "users": "user", "user": "user", "people": "person", "units": "unit", "unit": "unit"}


def _numeric_value(text: str) -> tuple[float, str] | None:
    """Parse a single clear scalar plus compatible unit; avoid arbitrary number picking."""
    matches = list(_NUMERIC_RE.finditer(text))
    matches = [m for m in matches if m.group("number") and (m.group("currency") or m.group("unit") or m.group("scale"))]
    if len(matches) != 1:
        return None
    m = matches[0]
    try:
        value = float(m.group("number").replace(",", "")) * _SCALE.get((m.group("scale") or "").lower(), 1)
    except ValueError:
        return None
    currency = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}.get(m.group("currency") or "", "")
    unit = _UNIT.get((m.group("unit") or "").lower(), (m.group("unit") or "").lower())
    # A currency amount with scale but no currency symbol still needs a distinct unit class.
    return value, currency or unit


def _temporal_rank(anchor: str | None) -> tuple[int, int, int] | None:
    if not anchor:
        return None
    text = anchor.strip().lower()
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        return tuple(map(int, iso.groups()))
    year = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
    if year:
        return int(year.group(1)), 0, 0
    return None


def _conflict_detail(a: DistilledClaim | dict, b: DistilledClaim) -> str | None:
    """Return a conservative conflict tag for matching subject/predicate claims."""
    get = lambda obj, key: getattr(obj, key) if hasattr(obj, key) else obj[key]
    if _norm(get(a, "subject")) != _norm(b.subject) or _norm(get(a, "predicate")) != _norm(b.predicate):
        return None
    old_obj, new_obj = get(a, "object_claim"), b.object_claim
    if _norm(old_obj) == _norm(new_obj):
        return None
    old_num, new_num = _numeric_value(old_obj), _numeric_value(new_obj)
    if old_num and new_num and old_num[1] == new_num[1] and old_num[0] != new_num[0]:
        return "numeric_discrepancy"
    old_neg, new_neg = _is_negated(old_obj), _is_negated(new_obj)
    if old_neg != new_neg:
        remove_neg = r"\b(not|no|never|neither|without|cannot|can't|doesn't|isn't|aren't|wasn't|weren't|didn't|failed to|unable to)\b"
        old_core = _norm(re.sub(remove_neg, "", old_obj))
        new_core = _norm(re.sub(remove_neg, "", new_obj))
        if old_core == new_core or old_core in new_core or new_core in old_core:
            return "polarity_conflict"
    return None


def _conflicts(a: DistilledClaim | dict, b: DistilledClaim) -> bool:
    return _conflict_detail(a, b) is not None


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
                    created_at TEXT NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'unresolved',
                    detail_tag TEXT NOT NULL DEFAULT 'polarity_conflict'
                );
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(contradiction_log)")}
            if "detail_tag" not in columns:
                conn.execute("ALTER TABLE contradiction_log ADD COLUMN detail_tag TEXT NOT NULL DEFAULT 'polarity_conflict'")

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
            rows = conn.execute("SELECT * FROM claims WHERE lower(subject)=lower(?) AND lower(predicate)=lower(?) ORDER BY confidence DESC, created_at DESC", (claim.subject, claim.predicate)).fetchall()
            for row in rows:
                prior = dict(row)
                prior["provenance_urls"] = json.loads(prior["provenance_urls"])
                if _norm(prior["object_claim"]) == _norm(claim.object_claim):
                    urls = list(dict.fromkeys(prior["provenance_urls"] + [claim.provenance_url]))
                    independent = any(_independent_source(url, claim.provenance_url) for url in prior["provenance_urls"])
                    reinforced = independent and claim.provenance_url not in prior["provenance_urls"]
                    confidence = min(0.95, prior["confidence"] + (0.05 if reinforced else 0.0))
                    conn.execute("UPDATE claims SET confidence=?,provenance_urls=?,updated_at=? WHERE id=?", (confidence, json.dumps(urls), now, prior["id"]))
                    return {"id": prior["id"], "status": prior["status"], "action": "corroborated" if reinforced else "duplicate_source", "confidence": confidence, "provenance_urls": urls}

                detail = _conflict_detail(prior, claim)
                if not detail:
                    continue
                old_rank, new_rank = _temporal_rank(prior.get("temporal_anchor")), _temporal_rank(claim.temporal_anchor)
                if old_rank and new_rank and new_rank > old_rank:
                    # A newer dated observation revises the older record; preserve both and do not label it a simultaneous contradiction.
                    conn.execute("UPDATE claims SET status='superseded_temporal',updated_at=? WHERE id=?", (now, prior["id"]))
                    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incoming_id, claim.subject, claim.predicate, claim.object_claim, claim.temporal_anchor, claim.confidence, claim.provenance_url, claim.domain, "unverified_claim", json.dumps([claim.provenance_url]), now, now))
                    return {"id": incoming_id, "status": "unverified_claim", "action": "temporal_revision", "supersedes": prior["id"], "detail_tag": "superseded_temporal"}
                if old_rank and new_rank and new_rank < old_rank:
                    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incoming_id, claim.subject, claim.predicate, claim.object_claim, claim.temporal_anchor, claim.confidence, claim.provenance_url, claim.domain, "superseded_temporal", json.dumps([claim.provenance_url]), now, now))
                    return {"id": incoming_id, "status": "superseded_temporal", "action": "older_temporal_evidence", "superseded_by": prior["id"], "detail_tag": "superseded_temporal"}
                if prior["confidence"] >= self.established_confidence:
                    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incoming_id, claim.subject, claim.predicate, claim.object_claim, claim.temporal_anchor, claim.confidence, claim.provenance_url, claim.domain, "conflicted_evidence", json.dumps([claim.provenance_url]), now, now))
                    incoming_json = {**payload, "id": incoming_id, "status": "conflicted_evidence", "detail_tag": detail}
                    conn.execute("INSERT INTO contradiction_log(id,existing_claim_id,incoming_claim_id,existing_claim_json,incoming_claim_json,created_at,detail_tag) VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), prior["id"], incoming_id, json.dumps(prior), json.dumps(incoming_json), now, detail))
                    conn.execute("UPDATE claims SET status='conflicted_evidence',updated_at=? WHERE id=?", (now, prior["id"]))
                    return {"id": incoming_id, "status": "conflicted_evidence", "action": "contradiction_logged", "conflicts_with": prior["id"], "detail_tag": detail}
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
            if item["status"] == "conflicted_evidence":
                item["warning"] = "Conflicted evidence: do not use as an unqualified fact; compare both source records."
            elif item["status"] == "unverified_claim":
                item["warning"] = "Unverified extracted claim: validate against source evidence before relying on it."
            elif item["status"] == "superseded_temporal":
                item["warning"] = "Older time-scoped evidence marked superseded by a newer dated record."
            else:
                item["warning"] = None
            result.append(item)
        return result

    def contradictions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM contradiction_log ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
