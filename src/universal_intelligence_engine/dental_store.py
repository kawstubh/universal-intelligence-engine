"""Dental persistence backend.

PostgreSQL is the production backend when DATABASE_URL is set. SQLite remains
supported for local development/tests. Database structure is owned by Alembic;
this module never creates tables directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _run_local_migrations() -> None:
    if os.getenv("DENTAL_AUTO_MIGRATE", "false").lower() != "true":
        return
    from alembic import command
    from alembic.config import Config

    candidates = []
    explicit = os.getenv("ALEMBIC_CONFIG", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.cwd() / "alembic.ini")
    candidates.append(Path(__file__).resolve().parents[2] / "alembic.ini")
    config_path = next((item for item in candidates if item.exists()), None)
    if config_path is None:
        raise RuntimeError("Alembic configuration not found; refusing to start without database migrations")
    cfg = Config(str(config_path))
    command.upgrade(cfg, "head")


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
        _run_local_migrations()

    @property
    def backend(self) -> str:
        return "postgres" if self.database_url else "sqlite"

    def _sqlite(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.database_url:
            with self._pg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = []
                    if cur.description:
                        columns = [d.name for d in cur.description]
                        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                conn.commit()
                return rows
        with self._sqlite() as conn:
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            conn.commit()
            return rows

    def patients(self, clinic_id: str) -> list[dict[str, Any]]:
        return self._execute(
            "SELECT id,name,phone,age,clinic_id,created_at FROM dental_patients WHERE clinic_id=%s ORDER BY created_at DESC"
            if self.database_url else
            "SELECT id,name,phone,age,clinic_id,created_at FROM dental_patients WHERE clinic_id=? ORDER BY created_at DESC",
            (clinic_id,),
        )

    def get_patient(self, patient_id: str, clinic_id: str) -> dict[str, Any] | None:
        rows = self._execute(
            "SELECT id,name,phone,age,clinic_id,created_at FROM dental_patients WHERE id=%s AND clinic_id=%s"
            if self.database_url else
            "SELECT id,name,phone,age,clinic_id,created_at FROM dental_patients WHERE id=? AND clinic_id=?",
            (patient_id, clinic_id),
        )
        return rows[0] if rows else None

    def create_patient(self, data: dict[str, Any], clinic_id: str) -> dict[str, Any]:
        item = {
            "id": _id(),
            "name": data.get("name", "").strip(),
            "phone": data.get("phone", "").strip(),
            "age": str(data.get("age", "")),
            "clinic_id": clinic_id,
            "created_at": _now(),
        }
        self._execute(
            "INSERT INTO dental_patients(id,name,phone,age,clinic_id,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_patients(id,name,phone,age,clinic_id,created_at) VALUES (?,?,?,?,?,?)",
            tuple(item[k] for k in ("id", "name", "phone", "age", "clinic_id", "created_at")),
        )
        return item

    def appointments(self, clinic_id: str, patient_id: str | None = None) -> list[dict[str, Any]]:
        if patient_id:
            sql = (
                "SELECT * FROM dental_appointments WHERE clinic_id=%s AND patient_id=%s ORDER BY starts_at ASC"
                if self.database_url else
                "SELECT * FROM dental_appointments WHERE clinic_id=? AND patient_id=? ORDER BY starts_at ASC"
            )
            return self._execute(sql, (clinic_id, patient_id))
        return self._execute(
            "SELECT * FROM dental_appointments WHERE clinic_id=%s ORDER BY starts_at ASC"
            if self.database_url else
            "SELECT * FROM dental_appointments WHERE clinic_id=? ORDER BY starts_at ASC",
            (clinic_id,),
        )

    def create_appointment(self, data: dict[str, Any], clinic_id: str) -> dict[str, Any]:
        item = {
            "id": _id(),
            "patient_id": data["patient_id"],
            "clinic_id": clinic_id,
            "starts_at": data["starts_at"],
            "treatment_type": data["treatment_type"],
            "note": data.get("note", ""),
            "status": data.get("status", "scheduled"),
            "created_at": _now(),
        }
        self._execute(
            "INSERT INTO dental_appointments(id,patient_id,clinic_id,starts_at,treatment_type,note,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_appointments(id,patient_id,clinic_id,starts_at,treatment_type,note,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            tuple(item[k] for k in ("id","patient_id","clinic_id","starts_at","treatment_type","note","status","created_at")),
        )
        return item

    def update_appointment(self, appointment_id: str, changes: dict[str, Any], clinic_id: str) -> dict[str, Any] | None:
        allowed = {"status", "starts_at", "note"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if not changes:
            return None
        set_parts, params = [], []
        for key, value in changes.items():
            set_parts.append(f"{key}=%s" if self.database_url else f"{key}=?")
            params.append(value)
        params.extend([appointment_id, clinic_id])
        sql = f"UPDATE dental_appointments SET {', '.join(set_parts)} WHERE id=" + ("%s" if self.database_url else "?") + " AND clinic_id=" + ("%s" if self.database_url else "?")
        self._execute(sql, tuple(params))
        query = "SELECT * FROM dental_appointments WHERE id=" + ("%s" if self.database_url else "?") + " AND clinic_id=" + ("%s" if self.database_url else "?")
        found = self._execute(query, (appointment_id, clinic_id))
        return found[0] if found else None

    def chart(self, patient_id: str, clinic_id: str) -> list[dict[str, Any]]:
        return self._execute(
            "SELECT * FROM dental_chart_entries WHERE patient_id=%s AND clinic_id=%s ORDER BY tooth_fdi"
            if self.database_url else
            "SELECT * FROM dental_chart_entries WHERE patient_id=? AND clinic_id=? ORDER BY tooth_fdi",
            (patient_id, clinic_id),
        )

    def save_chart(self, data: dict[str, Any], clinic_id: str) -> dict[str, Any]:
        patient_id, tooth_fdi = data["patient_id"], data["tooth_fdi"]
        self._execute(
            "DELETE FROM dental_chart_entries WHERE patient_id=%s AND tooth_fdi=%s AND clinic_id=%s"
            if self.database_url else
            "DELETE FROM dental_chart_entries WHERE patient_id=? AND tooth_fdi=? AND clinic_id=?",
            (patient_id, tooth_fdi, clinic_id),
        )
        item = {"id": _id(), **data, "clinic_id": clinic_id, "created_at": _now()}
        self._execute(
            "INSERT INTO dental_chart_entries(id,patient_id,clinic_id,tooth_fdi,status,note,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_chart_entries(id,patient_id,clinic_id,tooth_fdi,status,note,created_at) VALUES (?,?,?,?,?,?,?)",
            tuple(item[k] for k in ("id","patient_id","clinic_id","tooth_fdi","status","note","created_at")),
        )
        return item

    def periodontogram(self, patient_id: str, clinic_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM dental_periodontal_entries WHERE patient_id=%s AND clinic_id=%s ORDER BY tooth_fdi"
            if self.database_url else
            "SELECT * FROM dental_periodontal_entries WHERE patient_id=? AND clinic_id=? ORDER BY tooth_fdi",
            (patient_id, clinic_id),
        )
        for row in rows:
            try:
                row["measurements"] = json.loads(row.pop("measurements_json"))
            except Exception:
                row["measurements"] = {}
        return rows

    def save_periodontogram(self, data: dict[str, Any], clinic_id: str) -> dict[str, Any]:
        patient_id, tooth_fdi = data["patient_id"], data["tooth_fdi"]
        self._execute(
            "DELETE FROM dental_periodontal_entries WHERE patient_id=%s AND tooth_fdi=%s AND clinic_id=%s"
            if self.database_url else
            "DELETE FROM dental_periodontal_entries WHERE patient_id=? AND tooth_fdi=? AND clinic_id=?",
            (patient_id, tooth_fdi, clinic_id),
        )
        item = {
            "id": _id(), "patient_id": patient_id, "clinic_id": clinic_id,
            "tooth_fdi": tooth_fdi, "measurements": data.get("measurements", {}),
            "note": data.get("note", ""), "created_at": _now(),
        }
        self._execute(
            "INSERT INTO dental_periodontal_entries(id,patient_id,clinic_id,tooth_fdi,measurements_json,note,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_periodontal_entries(id,patient_id,clinic_id,tooth_fdi,measurements_json,note,created_at) VALUES (?,?,?,?,?,?,?)",
            (item["id"],patient_id,clinic_id,tooth_fdi,json.dumps(item["measurements"],ensure_ascii=False),item["note"],item["created_at"]),
        )
        return item

    def latest_otp(self, phone: str):
        rows = self._execute(
            "SELECT * FROM dental_otp_challenges WHERE phone=%s ORDER BY created_at DESC LIMIT 1"
            if self.database_url else
            "SELECT * FROM dental_otp_challenges WHERE phone=? ORDER BY created_at DESC LIMIT 1",
            (phone,),
        )
        return rows[0] if rows else None

    def create_otp(self, phone: str, otp_hash: str, challenge_id: str, created_at: int, expires_at: int):
        self._execute(
            "INSERT INTO dental_otp_challenges(id,phone,otp_hash,challenge_id,created_at,expires_at,attempts,consumed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_otp_challenges(id,phone,otp_hash,challenge_id,created_at,expires_at,attempts,consumed) VALUES (?,?,?,?,?,?,?,?)",
            (_id(), phone, otp_hash, challenge_id, str(created_at), str(expires_at), 0, False),
        )

    def get_otp(self, phone: str, challenge_id: str):
        rows = self._execute(
            "SELECT * FROM dental_otp_challenges WHERE phone=%s AND challenge_id=%s AND consumed=false"
            if self.database_url else
            "SELECT * FROM dental_otp_challenges WHERE phone=? AND challenge_id=? AND consumed=0",
            (phone, challenge_id),
        )
        return rows[0] if rows else None

    def increment_otp_attempt(self, row_id: str):
        self._execute(
            "UPDATE dental_otp_challenges SET attempts=attempts+1 WHERE id=%s"
            if self.database_url else
            "UPDATE dental_otp_challenges SET attempts=attempts+1 WHERE id=?",
            (row_id,),
        )

    def consume_otp(self, row_id: str):
        self._execute(
            "UPDATE dental_otp_challenges SET consumed=true WHERE id=%s"
            if self.database_url else
            "UPDATE dental_otp_challenges SET consumed=1 WHERE id=?",
            (row_id,),
        )

    def create_session(self, doctor_id: str, token_hash: str, expires_at: int):
        self._execute(
            "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (?,?,?,?)",
            (token_hash, doctor_id, str(expires_at), False),
        )

    def get_session(self, token_hash: str):
        rows = self._execute(
            "SELECT * FROM dental_sessions WHERE token_hash=%s" if self.database_url else
            "SELECT * FROM dental_sessions WHERE token_hash=?",
            (token_hash,),
        )
        return rows[0] if rows else None

    def doctor_by_email(self, email: str):
        rows = self._execute(
            "SELECT * FROM dental_doctors WHERE lower(email)=lower(%s) LIMIT 1"
            if self.database_url else
            "SELECT * FROM dental_doctors WHERE lower(email)=lower(?) LIMIT 1",
            (email,),
        )
        return rows[0] if rows else None

    def record_ai(self, patient_id: str | None, goal: str, result: dict[str, Any], clinic_id: str) -> None:
        self._execute(
            "INSERT INTO dental_ai_events(id,patient_id,clinic_id,goal,result_json,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url else
            "INSERT INTO dental_ai_events(id,patient_id,clinic_id,goal,result_json,created_at) VALUES (?,?,?,?,?,?)",
            (_id(), patient_id, clinic_id, goal, json.dumps(result, ensure_ascii=False), _now()),
        )

    def audit(self, actor_id: str | None, action: str, resource_type: str, resource_id: str | None = None) -> None:
        # Do not put patient names, phone numbers, clinical text, or AI prompts in audit metadata/logs.
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
