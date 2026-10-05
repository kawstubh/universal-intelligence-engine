"""Portable Dental persistence backend.

Production preference:
- DATABASE_URL -> PostgreSQL via psycopg
- otherwise -> SQLite for local/demo operation

The API never needs to know which storage engine is active.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS dental_patients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    age TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dental_appointments (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    treatment_type TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dental_chart_entries (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    tooth_fdi TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dental_ai_events (
    id TEXT PRIMARY KEY,
    patient_id TEXT,
    goal TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dental_audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dental_appointments_start ON dental_appointments(starts_at);
CREATE INDEX IF NOT EXISTS idx_dental_chart_patient ON dental_chart_entries(patient_id);
"""

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _id() -> str:
    return str(uuid.uuid4())

class DentalStore:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL")
        self.sqlite_path = os.getenv("DENTAL_SQLITE_PATH", "dental.sqlite3")
        self._pg = None
        if self.database_url:
            try:
                import psycopg
                self._pg = psycopg
            except ImportError as exc:
                raise RuntimeError("DATABASE_URL is set but psycopg is not installed") from exc
        self.initialize()

    @property
    def backend(self) -> str:
        return "postgresql" if self.database_url else "sqlite"

    def _sqlite(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        if self.database_url:
            with self._pg.connect(self.database_url) as conn:
                for statement in SCHEMA.split(";"):
                    statement = statement.strip()
                    if statement:
                        conn.execute(statement)
                conn.execute("ALTER TABLE dental_appointments ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT ''")
                conn.commit()
        else:
            with self._sqlite() as conn:
                conn.executescript(SCHEMA)
                try:
                    conn.execute("ALTER TABLE dental_appointments ADD COLUMN note TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError as exc:
                    if 'duplicate column name' not in str(exc).lower(): raise
                conn.commit()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.database_url:
            with self._pg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.description:
                        columns = [d.name for d in cur.description]
                        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                    else:
                        rows = []
                conn.commit()
                return rows
        with self._sqlite() as conn:
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            conn.commit()
            return rows

    def patients(self) -> list[dict[str, Any]]:
        return self._execute("SELECT * FROM dental_patients ORDER BY created_at DESC")

    def create_patient(self, data: dict[str, Any]) -> dict[str, Any]:
        item = {"id": _id(), **data, "created_at": _now()}
        self._execute(
            "INSERT INTO dental_patients(id,name,phone,age,created_at) VALUES (%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_patients(id,name,phone,age,created_at) VALUES (?,?,?,?,?)",
            tuple(item[k] for k in ("id","name","phone","age","created_at")),
        )
        return item

    def appointments(self) -> list[dict[str, Any]]:
        return self._execute("SELECT * FROM dental_appointments ORDER BY starts_at ASC")

    def create_appointment(self, data: dict[str, Any]) -> dict[str, Any]:
        item = {"id": _id(), "note": "", **data, "created_at": _now()}
        self._execute(
            "INSERT INTO dental_appointments(id,patient_id,starts_at,treatment_type,note,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_appointments(id,patient_id,starts_at,treatment_type,note,status,created_at) VALUES (?,?,?,?,?,?,?)",
            tuple(item[k] for k in ("id","patient_id","starts_at","treatment_type","note","status","created_at")),
        )
        return item

    def chart(self, patient_id: str) -> list[dict[str, Any]]:
        return self._execute(
            "SELECT * FROM dental_chart_entries WHERE patient_id=%s ORDER BY tooth_fdi"
            if self.database_url else
            "SELECT * FROM dental_chart_entries WHERE patient_id=? ORDER BY tooth_fdi",
            (patient_id,),
        )

    def save_chart(self, data: dict[str, Any]) -> dict[str, Any]:
        item = {"id": _id(), **data, "created_at": _now()}
        self._execute(
            "INSERT INTO dental_chart_entries(id,patient_id,tooth_fdi,status,note,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_chart_entries(id,patient_id,tooth_fdi,status,note,created_at) VALUES (?,?,?,?,?,?)",
            tuple(item[k] for k in ("id","patient_id","tooth_fdi","status","note","created_at")),
        )
        return item

    def record_ai(self, patient_id: str | None, goal: str, result: dict[str, Any]) -> None:
        self._execute(
            "INSERT INTO dental_ai_events(id,patient_id,goal,result_json,created_at) VALUES (%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_ai_events(id,patient_id,goal,result_json,created_at) VALUES (?,?,?,?,?)",
            (_id(), patient_id, goal, json.dumps(result, ensure_ascii=False), _now()),
        )

    def audit(self, actor_id: str | None, action: str, resource_type: str, resource_id: str | None = None) -> None:
        self._execute(
            "INSERT INTO dental_audit_events(id,actor_id,action,resource_type,resource_id,metadata_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_audit_events(id,actor_id,action,resource_type,resource_id,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (_id(), actor_id, action, resource_type, resource_id, "{}", _now()),
        )

_store: DentalStore | None = None

def get_dental_store() -> DentalStore:
    global _store
    if _store is None:
        _store = DentalStore()
    return _store
