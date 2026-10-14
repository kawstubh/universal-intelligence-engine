"""Persistent conservative domain heuristics distilled from independent evidence."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class HeuristicMemory:
    """Store auditable heuristics; unreviewed public evidence confidence is capped at 0.75."""

    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS heuristic_rules (
                id TEXT PRIMARY KEY, domain TEXT NOT NULL, subject TEXT NOT NULL,
                predicate TEXT NOT NULL, rule_text TEXT NOT NULL, confidence REAL NOT NULL,
                provenance_urls TEXT NOT NULL, rule_type TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(domain, subject, predicate, rule_type)
            )""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def consolidate(self, claim: dict, independent_sources: int | None = None, rule_type: str = "corroborated_claim") -> dict:
        urls = list(dict.fromkeys(claim.get("provenance_urls") or [claim.get("provenance_url", "")]))
        urls = [u for u in urls if u]
        sources = independent_sources if independent_sources is not None else len(urls)
        if sources < 2 and rule_type != "temporal_revision":
            return {"stored": False, "reason": "requires_independent_sources"}
        confidence = min(0.75, max(0.0, float(claim.get("confidence", 0.0))))
        if rule_type == "temporal_revision":
            rule = f"Prefer the newer dated evidence for {claim['subject']} / {claim['predicate']}; retain older versions as superseded and preserve provenance."
        else:
            rule = f"Treat {claim['subject']} / {claim['predicate']} as provisionally supported only when independently sourced; re-check time scope and contradictions."
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM heuristic_rules WHERE domain=? AND subject=? AND predicate=? AND rule_type=?", (claim.get("domain", "general_reference"), claim["subject"], claim["predicate"], rule_type)).fetchone()
            if existing:
                merged = list(dict.fromkeys(json.loads(existing["provenance_urls"]) + urls))
                confidence = min(0.75, max(confidence, float(existing["confidence"])))
                conn.execute("UPDATE heuristic_rules SET rule_text=?,confidence=?,provenance_urls=?,updated_at=? WHERE id=?", (rule, confidence, json.dumps(merged), now, existing["id"]))
                return {"stored": True, "id": existing["id"], "confidence": confidence, "action": "updated"}
            rule_id = str(uuid.uuid4())
            conn.execute("INSERT INTO heuristic_rules VALUES (?,?,?,?,?,?,?,?,?,?)", (rule_id, claim.get("domain", "general_reference"), claim["subject"], claim["predicate"], rule, confidence, json.dumps(urls), rule_type, now, now))
        return {"stored": True, "id": rule_id, "confidence": confidence, "action": "inserted"}

    def get_rules(self, domain: str | None = None) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM heuristic_rules WHERE (? IS NULL OR domain=?) ORDER BY updated_at DESC", (domain, domain)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provenance_urls"] = json.loads(item["provenance_urls"])
            result.append(item)
        return result
