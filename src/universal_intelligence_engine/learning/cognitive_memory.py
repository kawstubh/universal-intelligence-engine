"""Tripartite persistent cognitive memory: episodic traces, semantic graph, procedural rules."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .graph_store import KnowledgeGraphStore
from .heuristic_memory import HeuristicMemory
from ..security.provenance_crypto import ensure_integrity_ledger, append_integrity_event, verify_integrity_ledger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CognitiveMemoryEngine:
    """Unify durable episodic, semantic, and procedural memory over a shared SQLite file."""
    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.heuristics = HeuristicMemory(self.db_path)
        self.graph = KnowledgeGraphStore(self.db_path)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS uie_episodes (
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, domain TEXT, trace_json TEXT NOT NULL,
                trajectory_scores_json TEXT NOT NULL, chosen_path_index INTEGER, feedback_json TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            ensure_integrity_ledger(conn)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def record_episode(self, *, goal: str, domain: str | None = None, trace: Mapping[str, Any] | None = None,
                       trajectory_scores: list[Mapping[str, Any]] | None = None, chosen_path_index: int | None = None,
                       feedback: Mapping[str, Any] | None = None) -> dict:
        episode_id = str(uuid.uuid4())
        trace_data = dict(trace or {})
        scores_data = [dict(item) for item in (trajectory_scores or [])]
        feedback_data = dict(feedback or {})
        created = _now()
        payload = {"id": episode_id, "goal": goal, "domain": domain, "trace": trace_data,
                   "trajectory_scores": scores_data, "chosen_path_index": chosen_path_index,
                   "feedback": feedback_data, "created_at": created}
        with self._connect() as conn:
            conn.execute("INSERT INTO uie_episodes(id,goal,domain,trace_json,trajectory_scores_json,chosen_path_index,feedback_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (episode_id, goal, domain, json.dumps(trace_data, sort_keys=True), json.dumps(scores_data, sort_keys=True),
                          chosen_path_index, json.dumps(feedback_data, sort_keys=True), created))
            append_integrity_event(conn, "episode", episode_id, "INSERT", payload)
        return {"id": episode_id, "created_at": created}

    def recall_episodes(self, query: str | None = None, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM uie_episodes WHERE (? IS NULL OR lower(goal) LIKE ?) ORDER BY created_at DESC LIMIT ?",
                                (query, f"%{query.lower()}%" if query else None, max(1, min(int(limit), 100)))).fetchall()
        return [{"id": row["id"], "goal": row["goal"], "domain": row["domain"],
                 "trace": json.loads(row["trace_json"]), "trajectory_scores": json.loads(row["trajectory_scores_json"]),
                 "chosen_path_index": row["chosen_path_index"], "feedback": json.loads(row["feedback_json"]),
                 "created_at": row["created_at"]} for row in rows]

    def synthesize_constraint(self, *, domain: str, subject: str, predicate: str, rule_text: str,
                              provenance_urls: list[str] | None = None, confidence: float = 0.65) -> dict:
        return self.heuristics.store_constraint(domain=domain, subject=subject, predicate=predicate,
                                                rule_text=rule_text, provenance_urls=provenance_urls,
                                                confidence=confidence, rule_type="reflexion_constraint")

    def verify_integrity(self) -> tuple[bool, str | None]:
        with self._connect() as conn:
            return verify_integrity_ledger(conn)
