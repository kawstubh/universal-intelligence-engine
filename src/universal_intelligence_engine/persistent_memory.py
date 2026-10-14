"""Durable, domain-scoped lesson memory for UIE.

Lessons are proposals by default. Promotion to doctor_validated requires a
review token configured by the trusted deployment environment. This module
stores lessons only; callers must still apply authorization and safety policy.
"""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VALIDATION_STATUSES = {
    "unverified_proposed", "doctor_validated", "rejected", "deprecated"
}


class PersistentMemoryEngine:
    """SQLite-backed lesson store with exact-domain and status-filtered recall."""

    def __init__(self, db_path: str | Path = "uie_persistent_memory.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS uie_lessons (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL CHECK(length(trim(domain)) > 0),
                    trigger_pattern TEXT NOT NULL,
                    lesson_text TEXT NOT NULL CHECK(length(trim(lesson_text)) > 0),
                    validation_status TEXT NOT NULL DEFAULT 'unverified_proposed'
                        CHECK(validation_status IN
                        ('unverified_proposed','doctor_validated','rejected','deprecated')),
                    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
                    source_origin TEXT NOT NULL,
                    immutable_guardian_flag INTEGER NOT NULL DEFAULT 0
                        CHECK(immutable_guardian_flag IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewer_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_uie_lessons_domain_status
                    ON uie_lessons(domain, validation_status);
            """)

    def store_lesson(
        self,
        domain: str,
        trigger_pattern: Iterable[str],
        lesson_text: str,
        source_origin: str,
        confidence: float = 0.40,
    ) -> str:
        """Store a proposed lesson. This method cannot self-approve a lesson."""
        normalized_domain = domain.strip().lower()
        triggers = [str(item).strip().lower() for item in trigger_pattern if str(item).strip()]
        text = lesson_text.strip()
        origin = source_origin.strip()
        score = float(confidence)
        if not normalized_domain or not text or not origin:
            raise ValueError("domain, lesson_text and source_origin are required")
        if not triggers:
            raise ValueError("at least one non-empty trigger is required")
        if not 0.0 <= score <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        now = datetime.now(timezone.utc).isoformat()
        lesson_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO uie_lessons
                (id, domain, trigger_pattern, lesson_text, validation_status,
                 confidence, source_origin, immutable_guardian_flag, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'unverified_proposed', ?, ?, 0, ?, ?)""",
                (lesson_id, normalized_domain, json.dumps(triggers), text, score, origin, now, now),
            )
        return lesson_id

    def validate_lesson(self, lesson_id: str, reviewer_id: str, review_token: str) -> None:
        """Promote a proposal only when a deployment-configured review token matches."""
        expected = os.getenv("UIE_LESSON_REVIEW_TOKEN")
        if not expected or not review_token or not hmac.compare_digest(expected, review_token):
            raise PermissionError("authorized lesson review is not configured or token is invalid")
        if not reviewer_id.strip():
            raise ValueError("reviewer_id is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE uie_lessons SET validation_status='doctor_validated',
                   reviewer_id=?, updated_at=?
                   WHERE id=? AND validation_status='unverified_proposed'
                   AND immutable_guardian_flag=0""",
                (reviewer_id.strip(), now, lesson_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("lesson not found or not eligible for validation")

    def recall_lessons(
        self, domain: str, query: str, require_validated_only: bool = True, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Keyword recall scoped to an exact domain; no embeddings are implied."""
        normalized_domain = domain.strip().lower()
        terms = [term for term in query.lower().split() if len(term) > 2]
        if not normalized_domain or not terms:
            return []
        limit = max(1, min(int(limit), 50))
        status_clause = "AND validation_status='doctor_validated'" if require_validated_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM uie_lessons WHERE domain=? {status_clause} ORDER BY updated_at DESC LIMIT ?",
                (normalized_domain, limit * 10),
            ).fetchall()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            triggers = json.loads(item["trigger_pattern"])
            searchable = " ".join([item["lesson_text"].lower(), *triggers])
            score = sum(1 for term in set(terms) if term in searchable)
            if score:
                item["trigger_pattern"] = triggers
                item["match_terms"] = score
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        return [item for _, item in ranked[:limit]]
