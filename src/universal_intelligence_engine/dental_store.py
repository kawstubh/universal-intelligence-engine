"""Dental persistence backend.

PostgreSQL is the production backend when DATABASE_URL is set. SQLite remains
supported for local development/tests. Database structure is owned by Alembic;
this module never creates tables directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import string
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class DocumentReviewRequiredError(ValueError):
    """Raised when an unreviewed clinical document is shared with a patient."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _run_local_migrations() -> None:
    if os.getenv("DENTAL_AUTO_MIGRATE", "false").lower() != "true":
        return
    from alembic.config import Config

    from alembic import command

    candidates = []
    explicit = os.getenv("ALEMBIC_CONFIG", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.cwd() / "alembic.ini")
    candidates.append(Path(__file__).resolve().parents[2] / "alembic.ini")
    config_path = next((item for item in candidates if item.exists()), None)
    if config_path is None:
        raise RuntimeError(
            "Alembic configuration not found; refusing to start without database migrations"
        )
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
                raise RuntimeError(
                    "DATABASE_URL is set but psycopg is not installed"
                ) from exc
        _run_local_migrations()

    @property
    def backend(self) -> str:
        return "postgres" if self.database_url else "sqlite"

    def healthcheck(self) -> bool:
        """Return whether the configured persistence backend accepts a query."""
        rows = self._execute("SELECT 1 AS ok")
        return bool(rows and int(rows[0]["ok"]) == 1)

    def _sqlite(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self):
        """Run related Dental writes on one connection and commit them together."""
        if self.database_url:
            if self._pg is None:
                raise RuntimeError("PostgreSQL driver is not configured")
            with self._pg.connect(self.database_url) as conn:
                yield conn
            return

        conn = self._sqlite()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        connection=None,
    ) -> list[dict[str, Any]]:
        if connection is not None:
            if self.database_url:
                with connection.cursor() as cur:
                    cur.execute(sql, params)
                    rows = []
                    if cur.description:
                        columns = [d.name for d in cur.description]
                        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                    return rows
            cur = connection.execute(sql, params)
            return [dict(r) for r in cur.fetchall()] if cur.description else []

        if self.database_url:
            pg = self._pg
            if pg is None:
                raise RuntimeError("PostgreSQL driver is not configured")
            with pg.connect(self.database_url) as conn:
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
            if self.database_url
            else "SELECT id,name,phone,age,clinic_id,created_at FROM dental_patients WHERE clinic_id=? ORDER BY created_at DESC",
            (clinic_id,),
        )

    def get_patient(self, patient_id: str, clinic_id: str) -> dict[str, Any] | None:
        rows = self._execute(
            "SELECT id,name,phone,age,clinic_id,user_id,created_at FROM dental_patients WHERE id=%s AND clinic_id=%s"
            if self.database_url
            else "SELECT id,name,phone,age,clinic_id,user_id,created_at FROM dental_patients WHERE id=? AND clinic_id=?",
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
            if self.database_url
            else "INSERT INTO dental_patients(id,name,phone,age,clinic_id,created_at) VALUES (?,?,?,?,?,?)",
            tuple(
                item[k]
                for k in ("id", "name", "phone", "age", "clinic_id", "created_at")
            ),
        )
        return item

    def appointments(
        self, clinic_id: str, patient_id: str | None = None
    ) -> list[dict[str, Any]]:
        if patient_id:
            sql = (
                "SELECT * FROM dental_appointments WHERE clinic_id=%s AND patient_id=%s ORDER BY starts_at ASC"
                if self.database_url
                else "SELECT * FROM dental_appointments WHERE clinic_id=? AND patient_id=? ORDER BY starts_at ASC"
            )
            return self._execute(sql, (clinic_id, patient_id))
        return self._execute(
            "SELECT * FROM dental_appointments WHERE clinic_id=%s ORDER BY starts_at ASC"
            if self.database_url
            else "SELECT * FROM dental_appointments WHERE clinic_id=? ORDER BY starts_at ASC",
            (clinic_id,),
        )

    def create_appointment(
        self, data: dict[str, Any], clinic_id: str
    ) -> dict[str, Any]:
        if not self.get_patient(str(data["patient_id"]), clinic_id):
            raise ValueError("Patient not found in clinic")
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
            if self.database_url
            else "INSERT INTO dental_appointments(id,patient_id,clinic_id,starts_at,treatment_type,note,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            tuple(
                item[k]
                for k in (
                    "id",
                    "patient_id",
                    "clinic_id",
                    "starts_at",
                    "treatment_type",
                    "note",
                    "status",
                    "created_at",
                )
            ),
        )
        return item

    def update_appointment(
        self, appointment_id: str, changes: dict[str, Any], clinic_id: str
    ) -> dict[str, Any] | None:
        allowed = {"status", "starts_at", "note"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if not changes:
            return None
        set_parts, params = [], []
        for key, value in changes.items():
            set_parts.append(f"{key}=%s" if self.database_url else f"{key}=?")
            params.append(value)
        params.extend([appointment_id, clinic_id])
        sql = (
            f"UPDATE dental_appointments SET {', '.join(set_parts)} WHERE id="
            + ("%s" if self.database_url else "?")
            + " AND clinic_id="
            + ("%s" if self.database_url else "?")
        )
        self._execute(sql, tuple(params))
        query = (
            "SELECT * FROM dental_appointments WHERE id="
            + ("%s" if self.database_url else "?")
            + " AND clinic_id="
            + ("%s" if self.database_url else "?")
        )
        found = self._execute(query, (appointment_id, clinic_id))
        return found[0] if found else None

    def chart(self, patient_id: str, clinic_id: str) -> list[dict[str, Any]]:
        return self._execute(
            "SELECT * FROM dental_chart_entries WHERE patient_id=%s AND clinic_id=%s ORDER BY tooth_fdi"
            if self.database_url
            else "SELECT * FROM dental_chart_entries WHERE patient_id=? AND clinic_id=? ORDER BY tooth_fdi",
            (patient_id, clinic_id),
        )

    def save_chart(self, data: dict[str, Any], clinic_id: str) -> dict[str, Any]:
        patient_id, tooth_fdi = data["patient_id"], data["tooth_fdi"]
        if not self.get_patient(patient_id, clinic_id):
            raise ValueError("Patient not found in clinic")
        self._execute(
            "DELETE FROM dental_chart_entries WHERE patient_id=%s AND tooth_fdi=%s AND clinic_id=%s"
            if self.database_url
            else "DELETE FROM dental_chart_entries WHERE patient_id=? AND tooth_fdi=? AND clinic_id=?",
            (patient_id, tooth_fdi, clinic_id),
        )
        item = {"id": _id(), **data, "clinic_id": clinic_id, "created_at": _now()}
        self._execute(
            "INSERT INTO dental_chart_entries(id,patient_id,clinic_id,tooth_fdi,status,note,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_chart_entries(id,patient_id,clinic_id,tooth_fdi,status,note,created_at) VALUES (?,?,?,?,?,?,?)",
            tuple(
                item[k]
                for k in (
                    "id",
                    "patient_id",
                    "clinic_id",
                    "tooth_fdi",
                    "status",
                    "note",
                    "created_at",
                )
            ),
        )
        return item

    def periodontogram(self, patient_id: str, clinic_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM dental_periodontal_entries WHERE patient_id=%s AND clinic_id=%s ORDER BY tooth_fdi"
            if self.database_url
            else "SELECT * FROM dental_periodontal_entries WHERE patient_id=? AND clinic_id=? ORDER BY tooth_fdi",
            (patient_id, clinic_id),
        )
        for row in rows:
            try:
                row["measurements"] = json.loads(row.pop("measurements_json"))
            except (json.JSONDecodeError, TypeError, KeyError):
                row["measurements"] = {}
        return rows

    def save_periodontogram(
        self, data: dict[str, Any], clinic_id: str
    ) -> dict[str, Any]:
        patient_id, tooth_fdi = data["patient_id"], data["tooth_fdi"]
        if not self.get_patient(patient_id, clinic_id):
            raise ValueError("Patient not found in clinic")
        self._execute(
            "DELETE FROM dental_periodontal_entries WHERE patient_id=%s AND tooth_fdi=%s AND clinic_id=%s"
            if self.database_url
            else "DELETE FROM dental_periodontal_entries WHERE patient_id=? AND tooth_fdi=? AND clinic_id=?",
            (patient_id, tooth_fdi, clinic_id),
        )
        item = {
            "id": _id(),
            "patient_id": patient_id,
            "clinic_id": clinic_id,
            "tooth_fdi": tooth_fdi,
            "measurements": data.get("measurements", {}),
            "note": data.get("note", ""),
            "created_at": _now(),
        }
        self._execute(
            "INSERT INTO dental_periodontal_entries(id,patient_id,clinic_id,tooth_fdi,measurements_json,note,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_periodontal_entries(id,patient_id,clinic_id,tooth_fdi,measurements_json,note,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                item["id"],
                patient_id,
                clinic_id,
                tooth_fdi,
                json.dumps(item["measurements"], ensure_ascii=False),
                item["note"],
                item["created_at"],
            ),
        )
        return item

    def organisations_for_doctor(self, doctor_id: str) -> list[dict[str, Any]]:
        sql = (
            "SELECT DISTINCT o.id, o.name, o.organisation_type, o.active, o.created_at "
            "FROM dental_organisations o "
            "JOIN dental_clinics c ON c.organisation_id=o.id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.doctor_id=%s AND m.active=true AND c.active=true AND o.active=true "
            "AND (home.organisation_id=o.id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=o.id))) "
            "ORDER BY o.name"
            if self.database_url
            else "SELECT DISTINCT o.id, o.name, o.organisation_type, o.active, o.created_at "
            "FROM dental_organisations o "
            "JOIN dental_clinics c ON c.organisation_id=o.id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.doctor_id=? AND m.active=1 AND c.active=1 AND o.active=1 "
            "AND (home.organisation_id=o.id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=o.id))) "
            "ORDER BY o.name"
        )
        return self._execute(sql, (doctor_id,))

    def workspaces_for_doctor(self, doctor_id: str) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.id, c.id AS clinic_id, c.organisation_id, c.name, "
            "c.clinic_type, c.parent_clinic_id, c.active, c.created_at, "
            "o.name AS organisation_name, o.organisation_type, m.role "
            "FROM dental_clinics c "
            "JOIN dental_organisations o ON o.id=c.organisation_id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.doctor_id=%s AND m.active=true AND c.active=true AND o.active=true "
            "AND (home.organisation_id=c.organisation_id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=c.organisation_id))) "
            "ORDER BY o.name, c.name"
            if self.database_url
            else "SELECT c.id, c.id AS clinic_id, c.organisation_id, c.name, "
            "c.clinic_type, c.parent_clinic_id, c.active, c.created_at, "
            "o.name AS organisation_name, o.organisation_type, m.role "
            "FROM dental_clinics c "
            "JOIN dental_organisations o ON o.id=c.organisation_id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.doctor_id=? AND m.active=1 AND c.active=1 AND o.active=1 "
            "AND (home.organisation_id=c.organisation_id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=c.organisation_id))) "
            "ORDER BY o.name, c.name"
        )
        return self._execute(sql, (doctor_id,))

    def workspaces_for_organisation(self, organisation_id: str) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.id, c.organisation_id, c.name, c.clinic_type, "
            "c.parent_clinic_id, c.active, c.created_at, "
            "o.name AS organisation_name, o.organisation_type, "
            "(SELECT COUNT(*) FROM dental_doctor_memberships m "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.clinic_id=c.id AND m.active=true AND d.active=true AND d.role IN ('doctor','dentist') "
            "AND (home.organisation_id=c.organisation_id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=c.organisation_id)))) AS active_doctors "
            "FROM dental_clinics c "
            "JOIN dental_organisations o ON o.id=c.organisation_id "
            "WHERE c.organisation_id=%s AND c.active=true AND o.active=true "
            "ORDER BY c.name"
            if self.database_url
            else "SELECT c.id, c.organisation_id, c.name, c.clinic_type, "
            "c.parent_clinic_id, c.active, c.created_at, "
            "o.name AS organisation_name, o.organisation_type, "
            "(SELECT COUNT(*) FROM dental_doctor_memberships m "
            "JOIN dental_doctors d ON d.id=m.doctor_id "
            "JOIN dental_clinics home ON home.id=d.clinic_id "
            "WHERE m.clinic_id=c.id AND m.active=1 AND d.active=1 AND d.role IN ('doctor','dentist') "
            "AND (home.organisation_id=c.organisation_id OR (m.role='owner' AND EXISTS ("
            "SELECT 1 FROM dental_audit_events a WHERE a.actor_id=m.doctor_id "
            "AND a.action='organisation.created' AND a.resource_type='organisation' "
            "AND a.resource_id=c.organisation_id)))) AS active_doctors "
            "FROM dental_clinics c "
            "JOIN dental_organisations o ON o.id=c.organisation_id "
            "WHERE c.organisation_id=? AND c.active=1 AND o.active=1 "
            "ORDER BY c.name"
        )
        return self._execute(sql, (organisation_id,))

    def doctor_membership(
        self, doctor_id: str, clinic_id: str
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT id, doctor_id, clinic_id, role, active, created_at "
            "FROM dental_doctor_memberships WHERE doctor_id=%s AND clinic_id=%s"
            if self.database_url
            else "SELECT id, doctor_id, clinic_id, role, active, created_at "
            "FROM dental_doctor_memberships WHERE doctor_id=? AND clinic_id=?"
        )
        rows = self._execute(sql, (doctor_id, clinic_id))
        return rows[0] if rows else None

    def create_organisation(self, data: dict[str, Any], connection=None) -> dict[str, Any]:
        item = {
            "id": data.get("id") or _id(),
            "name": data["name"].strip(),
            "organisation_type": data["organisation_type"],
            "active": data.get("active", True),
            "created_at": data.get("created_at") or _now(),
        }
        sql = (
            "INSERT INTO dental_organisations(id,name,organisation_type,active,created_at) "
            "VALUES (%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_organisations(id,name,organisation_type,active,created_at) "
            "VALUES (?,?,?,?,?)"
        )
        self._execute(
            sql,
            tuple(
                item[key]
                for key in ("id", "name", "organisation_type", "active", "created_at")
            ),
            connection=connection,
        )
        return item

    def create_clinic(self, data: dict[str, Any], connection=None) -> dict[str, Any]:
        item = {
            "id": data.get("id") or _id(),
            "organisation_id": data["organisation_id"],
            "name": data["name"].strip(),
            "clinic_type": data.get("clinic_type", "clinic"),
            "parent_clinic_id": data.get("parent_clinic_id"),
            "active": data.get("active", True),
            "created_at": data.get("created_at") or _now(),
        }
        sql = (
            "INSERT INTO dental_clinics"
            "(id,organisation_id,name,clinic_type,parent_clinic_id,active,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_clinics"
            "(id,organisation_id,name,clinic_type,parent_clinic_id,active,created_at) "
            "VALUES (?,?,?,?,?,?,?)"
        )
        self._execute(
            sql,
            tuple(
                item[key]
                for key in (
                    "id",
                    "organisation_id",
                    "name",
                    "clinic_type",
                    "parent_clinic_id",
                    "active",
                    "created_at",
                )
            ),
            connection=connection,
        )
        return item

    def create_doctor_membership(
        self, doctor_id: str, clinic_id: str, role: str, connection=None
    ) -> dict[str, Any]:
        item = {
            "id": _id(),
            "doctor_id": doctor_id,
            "clinic_id": clinic_id,
            "role": role,
            "active": True,
            "created_at": _now(),
        }
        sql = (
            "INSERT INTO dental_doctor_memberships"
            "(id,doctor_id,clinic_id,role,active,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_doctor_memberships"
            "(id,doctor_id,clinic_id,role,active,created_at) "
            "VALUES (?,?,?,?,?,?)"
        )
        self._execute(
            sql,
            tuple(
                item[key]
                for key in ("id", "doctor_id", "clinic_id", "role", "active", "created_at")
            ),
            connection=connection,
        )
        return item

    def set_doctor_membership_active(
        self, doctor_id: str, clinic_id: str, active: bool, connection=None
    ) -> None:
        sql = (
            "UPDATE dental_doctor_memberships SET active=%s "
            "WHERE doctor_id=%s AND clinic_id=%s"
            if self.database_url
            else "UPDATE dental_doctor_memberships SET active=? "
            "WHERE doctor_id=? AND clinic_id=?"
        )
        self._execute(sql, (active, doctor_id, clinic_id), connection=connection)

    def create_session(
        self, doctor_id: str, token_hash: str, expires_at: int, connection=None
    ):
        self._execute(
            "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (?,?,?,?)",
            (token_hash, doctor_id, str(expires_at), False),
            connection=connection,
        )

    def get_session(self, token_hash: str):
        rows = self._execute(
            "SELECT * FROM dental_sessions WHERE token_hash=%s"
            if self.database_url
            else "SELECT * FROM dental_sessions WHERE token_hash=?",
            (token_hash,),
        )
        return rows[0] if rows else None

    def revoke_session(self, token_hash: str, connection=None) -> None:
        self._execute(
            "UPDATE dental_sessions SET revoked=true WHERE token_hash=%s"
            if self.database_url
            else "UPDATE dental_sessions SET revoked=1 WHERE token_hash=?",
            (token_hash,),
            connection=connection,
        )

    def doctor_by_email(self, email: str):
        rows = self._execute(
            "SELECT * FROM dental_doctors WHERE lower(email)=lower(%s) LIMIT 1"
            if self.database_url
            else "SELECT * FROM dental_doctors WHERE lower(email)=lower(?) LIMIT 1",
            (email,),
        )
        return rows[0] if rows else None

    def doctor_by_id(self, doctor_id: str):
        rows = self._execute(
            "SELECT * FROM dental_doctors WHERE id=%s LIMIT 1"
            if self.database_url
            else "SELECT * FROM dental_doctors WHERE id=? LIMIT 1",
            (doctor_id,),
        )
        return rows[0] if rows else None

    def create_doctor(self, data: dict[str, Any], connection=None) -> dict[str, Any]:
        item = {
            "id": data["id"],
            "email": data["email"].strip().lower(),
            "phone": data.get("phone", ""),
            "password_hash": data["password_hash"],
            "role": data.get("role", "doctor"),
            "clinic_id": data["clinic_id"],
            "active": True,
            "created_at": _now(),
        }
        self._execute(
            "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,active,created_at) VALUES (?,?,?,?,?,?,?,?)",
            tuple(
                item[k]
                for k in (
                    "id",
                    "email",
                    "phone",
                    "password_hash",
                    "role",
                    "clinic_id",
                    "active",
                    "created_at",
                )
            ),
            connection=connection,
        )
        return item

    def set_doctor_active(self, doctor_id: str, active: bool) -> None:
        self._execute(
            "UPDATE dental_doctors SET active=%s WHERE id=%s"
            if self.database_url
            else "UPDATE dental_doctors SET active=? WHERE id=?",
            (active, doctor_id),
        )

    def record_ai(
        self, patient_id: str | None, goal: str, result: dict[str, Any], clinic_id: str
    ) -> None:
        self._execute(
            "INSERT INTO dental_ai_events(id,patient_id,clinic_id,goal,result_json,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_ai_events(id,patient_id,clinic_id,goal,result_json,created_at) VALUES (?,?,?,?,?,?)",
            (
                _id(),
                patient_id,
                clinic_id,
                goal,
                json.dumps(result, ensure_ascii=False),
                _now(),
            ),
        )

    def audit(
        self,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        clinic_id: str | None = None,
        connection=None,
    ) -> None:
        # Do not put patient names, phone numbers, clinical text, or AI prompts in audit metadata/logs.
        self._execute(
            "INSERT INTO dental_audit_events(id,actor_id,action,resource_type,resource_id,clinic_id,metadata_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_audit_events(id,actor_id,action,resource_type,resource_id,clinic_id,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (_id(), actor_id, action, resource_type, resource_id, clinic_id, "{}", _now()),
            connection=connection,
        )


    def _treatment_plan_items(self, plan_id: str, clinic_id: str, connection=None):
        sql = (
            "SELECT id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at "
            "FROM dental_treatment_plan_items WHERE clinic_id=%s AND plan_id=%s ORDER BY sort_order,id"
            if self.database_url
            else "SELECT id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at "
            "FROM dental_treatment_plan_items WHERE clinic_id=? AND plan_id=? ORDER BY sort_order,id"
        )
        return self._execute(sql, (clinic_id, plan_id), connection=connection)

    def treatment_plans(
        self, clinic_id: str, patient_id: str, patient_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        visibility = (
            " AND patient_visible=true AND status <> 'draft' AND status <> 'cancelled'"
            if self.database_url
            else " AND patient_visible=1 AND status NOT IN ('draft','cancelled')"
        ) if patient_visible_only else ""
        sql = (
            "SELECT * FROM dental_treatment_plans WHERE clinic_id=%s AND patient_id=%s"
            + visibility + " ORDER BY created_at DESC"
            if self.database_url
            else "SELECT * FROM dental_treatment_plans WHERE clinic_id=? AND patient_id=?"
            + visibility + " ORDER BY created_at DESC"
        )
        plans = self._execute(sql, (clinic_id, patient_id))
        for plan in plans:
            plan["items"] = self._treatment_plan_items(plan["id"], clinic_id)
        return plans

    def create_treatment_plan(
        self, data: dict[str, Any], clinic_id: str, doctor_id: str
    ) -> dict[str, Any]:
        plan_id = _id()
        now = _now()
        with self.transaction() as connection:
            patient_sql = (
                "SELECT id FROM dental_patients WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "SELECT id FROM dental_patients WHERE id=? AND clinic_id=?"
            )
            if not self._execute(patient_sql, (data["patient_id"], clinic_id), connection=connection):
                raise ValueError("Patient not found in this workspace")
            plan = {
                "id": plan_id,
                "clinic_id": clinic_id,
                "patient_id": data["patient_id"],
                "created_by_doctor_id": doctor_id,
                "title": data["title"].strip(),
                "summary": data.get("summary", "").strip(),
                "status": "draft",
                "patient_visible": False,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            sql = (
                "INSERT INTO dental_treatment_plans(id,clinic_id,patient_id,created_by_doctor_id,title,summary,status,patient_visible,version,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                if self.database_url
                else "INSERT INTO dental_treatment_plans(id,clinic_id,patient_id,created_by_doctor_id,title,summary,status,patient_visible,version,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            )
            self._execute(sql, tuple(plan[key] for key in (
                "id","clinic_id","patient_id","created_by_doctor_id","title","summary",
                "status","patient_visible","version","created_at","updated_at"
            )), connection=connection)
            for index, item in enumerate(data.get("items", [])):
                item_row = {
                    "id": _id(),
                    "clinic_id": clinic_id,
                    "plan_id": plan_id,
                    "service_code": item.get("service_code", "").strip(),
                    "description": item["description"].strip(),
                    "tooth_fdi": item.get("tooth_fdi"),
                    "status": "planned",
                    "sort_order": index,
                    "created_at": now,
                    "updated_at": now,
                }
                item_sql = (
                    "INSERT INTO dental_treatment_plan_items(id,clinic_id,plan_id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    if self.database_url
                    else "INSERT INTO dental_treatment_plan_items(id,clinic_id,plan_id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)"
                )
                self._execute(item_sql, tuple(item_row[key] for key in (
                    "id","clinic_id","plan_id","service_code","description","tooth_fdi",
                    "status","sort_order","created_at","updated_at"
                )), connection=connection)
            self.audit(doctor_id, "treatment_plan.created", "treatment_plan", plan_id, clinic_id, connection)
            plan["items"] = self._treatment_plan_items(plan_id, clinic_id, connection)
        return plan

    def update_treatment_plan(
        self, plan_id: str, clinic_id: str, doctor_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {"title", "summary", "status", "patient_visible", "items"}
        expected_version = changes.pop("expected_version", None)
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return None
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT * FROM dental_treatment_plans WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT * FROM dental_treatment_plans WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (plan_id, clinic_id), connection=connection)
            if not rows:
                return None
            current = rows[0]
            if expected_version is not None and int(current["version"]) != int(expected_version):
                raise ValueError("Treatment plan version conflict; reload before saving")
            next_status = changes.get("status", current["status"])
            transitions = {
                "draft": {"proposed", "cancelled"},
                "proposed": {"accepted", "cancelled"},
                "accepted": {"in_progress", "cancelled"},
                "in_progress": {"completed", "cancelled"},
                "completed": set(),
                "cancelled": set(),
            }
            if next_status != current["status"] and next_status not in transitions.get(current["status"], set()):
                raise ValueError(f"Treatment plan cannot transition from {current['status']} to {next_status}")
            if changes.get("patient_visible") is True and next_status in {"draft", "cancelled"}:
                raise ValueError("A treatment plan must be proposed before it can be shared")
            items = changes.pop("items", None)
            if items is not None and current["status"] != "draft":
                raise ValueError("Treatment plan items can only be edited while the plan is a draft")
            set_parts, params = [], []
            for key, value in changes.items():
                set_parts.append(f"{key}=" + ("%s" if self.database_url else "?"))
                params.append(value)
            set_parts.extend([
                "version=version+1",
                "updated_at=" + ("%s" if self.database_url else "?"),
            ])
            params.append(_now())
            params.extend([plan_id, clinic_id])
            update_sql = (
                "UPDATE dental_treatment_plans SET " + ",".join(set_parts) + " WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "UPDATE dental_treatment_plans SET " + ",".join(set_parts) + " WHERE id=? AND clinic_id=?"
            )
            self._execute(update_sql, tuple(params), connection=connection)
            if items is not None:
                delete_sql = (
                    "DELETE FROM dental_treatment_plan_items WHERE clinic_id=%s AND plan_id=%s"
                    if self.database_url
                    else "DELETE FROM dental_treatment_plan_items WHERE clinic_id=? AND plan_id=?"
                )
                self._execute(delete_sql, (clinic_id, plan_id), connection=connection)
                now = _now()
                for index, item in enumerate(items):
                    item_row = {
                        "id": _id(), "clinic_id": clinic_id, "plan_id": plan_id,
                        "service_code": item.get("service_code", "").strip(),
                        "description": item["description"].strip(), "tooth_fdi": item.get("tooth_fdi"),
                        "status": "planned", "sort_order": index, "created_at": now, "updated_at": now,
                    }
                    item_sql = (
                        "INSERT INTO dental_treatment_plan_items(id,clinic_id,plan_id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        if self.database_url
                        else "INSERT INTO dental_treatment_plan_items(id,clinic_id,plan_id,service_code,description,tooth_fdi,status,sort_order,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)"
                    )
                    self._execute(item_sql, tuple(item_row[key] for key in (
                        "id","clinic_id","plan_id","service_code","description","tooth_fdi",
                        "status","sort_order","created_at","updated_at"
                    )), connection=connection)
            self.audit(doctor_id, "treatment_plan.updated", "treatment_plan", plan_id, clinic_id, connection)
            rows = self._execute(sql, (plan_id, clinic_id), connection=connection)
            plan = rows[0] if rows else None
            if plan:
                plan["items"] = self._treatment_plan_items(plan_id, clinic_id, connection)
            return plan

    def _invoice_items(self, invoice_id: str, clinic_id: str, connection=None):
        sql = (
            "SELECT id,description,quantity_milli,unit_price_paise,tax_rate_bps,subtotal_paise,discount_paise,tax_paise,total_paise,sort_order,created_at "
            "FROM dental_invoice_items WHERE clinic_id=%s AND invoice_id=%s ORDER BY sort_order,id"
            if self.database_url
            else "SELECT id,description,quantity_milli,unit_price_paise,tax_rate_bps,subtotal_paise,discount_paise,tax_paise,total_paise,sort_order,created_at "
            "FROM dental_invoice_items WHERE clinic_id=? AND invoice_id=? ORDER BY sort_order,id"
        )
        return self._execute(sql, (clinic_id, invoice_id), connection=connection)

    def _invoice_receipts(self, invoice_id: str, clinic_id: str, connection=None):
        sql = (
            "SELECT id,receipt_number,invoice_id,patient_id,amount_paise,method,reference,idempotency_key,paid_at,created_at "
            "FROM dental_receipts WHERE clinic_id=%s AND invoice_id=%s ORDER BY paid_at,id"
            if self.database_url
            else "SELECT id,receipt_number,invoice_id,patient_id,amount_paise,method,reference,idempotency_key,paid_at,created_at "
            "FROM dental_receipts WHERE clinic_id=? AND invoice_id=? ORDER BY paid_at,id"
        )
        return self._execute(sql, (clinic_id, invoice_id), connection=connection)

    def get_invoice(self, invoice_id: str, clinic_id: str, connection=None):
        sql = (
            "SELECT * FROM dental_invoices WHERE id=%s AND clinic_id=%s"
            if self.database_url
            else "SELECT * FROM dental_invoices WHERE id=? AND clinic_id=?"
        )
        rows = self._execute(sql, (invoice_id, clinic_id), connection=connection)
        if not rows:
            return None
        invoice = rows[0]
        invoice["items"] = self._invoice_items(invoice_id, clinic_id, connection)
        invoice["receipts"] = self._invoice_receipts(invoice_id, clinic_id, connection)
        invoice["balance_paise"] = int(invoice["total_paise"]) - int(invoice["paid_paise"])
        return invoice

    def invoices(
        self, clinic_id: str, patient_id: str, patient_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        visibility = (
            " AND patient_visible=true AND status NOT IN ('draft','void')"
            if self.database_url
            else " AND patient_visible=1 AND status NOT IN ('draft','void')"
        ) if patient_visible_only else ""
        sql = (
            "SELECT id,clinic_id,patient_id,created_by_doctor_id,invoice_number,idempotency_key,currency,status,subtotal_paise,discount_paise,tax_paise,total_paise,paid_paise,patient_visible,patient_note,due_date,issued_at,voided_at,created_at,updated_at "
            "FROM dental_invoices WHERE clinic_id=%s AND patient_id=%s" + visibility + " ORDER BY created_at DESC"
            if self.database_url
            else "SELECT id,clinic_id,patient_id,created_by_doctor_id,invoice_number,idempotency_key,currency,status,subtotal_paise,discount_paise,tax_paise,total_paise,paid_paise,patient_visible,patient_note,due_date,issued_at,voided_at,created_at,updated_at "
            "FROM dental_invoices WHERE clinic_id=? AND patient_id=?" + visibility + " ORDER BY created_at DESC"
        )
        rows = self._execute(sql, (clinic_id, patient_id))
        result = []
        for row in rows:
            row["items"] = self._invoice_items(row["id"], clinic_id)
            row["receipts"] = self._invoice_receipts(row["id"], clinic_id)
            row["balance_paise"] = int(row["total_paise"]) - int(row["paid_paise"])
            result.append(row)
        return result

    @staticmethod
    def _invoice_request_matches(existing: dict[str, Any] | None, requested: dict[str, Any], items: list[dict[str, Any]]) -> bool:
        if not existing:
            return False
        keys = ("patient_id", "subtotal_paise", "discount_paise", "tax_paise", "total_paise", "patient_note")
        if any(str(existing.get(key, "")) != str(requested.get(key, "")) for key in keys):
            return False
        fields = ("description", "quantity_milli", "unit_price_paise", "tax_rate_bps", "subtotal_paise", "discount_paise", "tax_paise", "total_paise")
        existing_items = existing.get("items", [])
        if len(existing_items) != len(items):
            return False
        return all(
            all(str(left.get(field, "")) == str(right.get(field, "")) for field in fields)
            for left, right in zip(existing_items, items)
        )

    def create_invoice(
        self, data: dict[str, Any], clinic_id: str, doctor_id: str
    ) -> dict[str, Any]:
        items = data.get("items") or []
        if not items:
            raise ValueError("An invoice must contain at least one line item")
        subtotal = 0
        tax_total = 0
        calculated_items = []
        now = _now()
        for index, item in enumerate(items):
            quantity = int(item["quantity_milli"])
            unit_price = int(item["unit_price_paise"])
            tax_rate = int(item.get("tax_rate_bps", 0))
            if quantity <= 0 or unit_price < 0 or tax_rate < 0 or tax_rate > 2800:
                raise ValueError("Invoice line item amount is invalid")
            line_subtotal = (quantity * unit_price + 500) // 1000
            subtotal += line_subtotal
            calculated_items.append({
                "id": _id(), "clinic_id": clinic_id, "description": item["description"].strip(),
                "quantity_milli": quantity, "unit_price_paise": unit_price,
                "tax_rate_bps": tax_rate, "subtotal_paise": line_subtotal,
                "discount_paise": 0, "tax_paise": 0, "total_paise": 0,
                "sort_order": index, "created_at": now,
            })
        discount = int(data.get("discount_paise", 0))
        if discount < 0 or discount > subtotal:
            raise ValueError("Invoice discount must not exceed the pre-tax subtotal")
        remaining_discount = discount
        for index, item in enumerate(calculated_items):
            if index == len(calculated_items) - 1:
                line_discount = remaining_discount
            elif subtotal:
                line_discount = (discount * int(item["subtotal_paise"])) // subtotal
                remaining_discount -= line_discount
            else:
                line_discount = 0
            taxable = int(item["subtotal_paise"]) - line_discount
            line_tax = (taxable * int(item["tax_rate_bps"]) + 5000) // 10000
            item["discount_paise"] = line_discount
            item["tax_paise"] = line_tax
            item["total_paise"] = taxable + line_tax
            tax_total += line_tax
        total = subtotal - discount + tax_total
        if total <= 0:
            raise ValueError("Invoice total must be greater than zero")
        invoice_id = _id()
        invoice_number = "INV-" + datetime.now(UTC).strftime("%Y%m") + "-" + uuid.uuid4().hex[:8].upper()
        idempotency_key = str(data["idempotency_key"]).strip()
        if len(idempotency_key) < 16 or len(idempotency_key) > 128:
            raise ValueError("Invoice idempotency key must be 16-128 characters")
        invoice = {
            "id": invoice_id, "clinic_id": clinic_id, "patient_id": data["patient_id"],
            "created_by_doctor_id": doctor_id, "invoice_number": invoice_number,
            "idempotency_key": idempotency_key, "currency": "INR", "status": "draft", "subtotal_paise": subtotal,
            "discount_paise": discount, "tax_paise": tax_total, "total_paise": total,
            "paid_paise": 0, "patient_visible": False, "patient_note": data.get("patient_note", "").strip(),
            "due_date": None, "issued_at": None, "voided_at": None,
            "created_at": now, "updated_at": now,
        }
        with self.transaction() as connection:
            patient_sql = (
                "SELECT id FROM dental_patients WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "SELECT id FROM dental_patients WHERE id=? AND clinic_id=?"
            )
            if not self._execute(patient_sql, (data["patient_id"], clinic_id), connection=connection):
                raise ValueError("Patient not found in this workspace")
            existing_sql = (
                "SELECT id FROM dental_invoices WHERE clinic_id=%s AND idempotency_key=%s"
                if self.database_url
                else "SELECT id FROM dental_invoices WHERE clinic_id=? AND idempotency_key=?"
            )
            existing = self._execute(existing_sql, (clinic_id, idempotency_key), connection=connection)
            if existing:
                existing_invoice = self.get_invoice(existing[0]["id"], clinic_id, connection=connection)
                if not self._invoice_request_matches(existing_invoice, invoice, calculated_items):
                    raise ValueError("Invoice idempotency key was already used for a different invoice")
                return existing_invoice
            invoice_sql = (
                "INSERT INTO dental_invoices(id,clinic_id,patient_id,created_by_doctor_id,invoice_number,idempotency_key,currency,status,subtotal_paise,discount_paise,tax_paise,total_paise,paid_paise,patient_visible,patient_note,due_date,issued_at,voided_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (clinic_id,idempotency_key) DO NOTHING RETURNING id"
                if self.database_url
                else "INSERT INTO dental_invoices(id,clinic_id,patient_id,created_by_doctor_id,invoice_number,idempotency_key,currency,status,subtotal_paise,discount_paise,tax_paise,total_paise,paid_paise,patient_visible,patient_note,due_date,issued_at,voided_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(clinic_id,idempotency_key) DO NOTHING RETURNING id"
            )
            inserted = self._execute(invoice_sql, tuple(invoice[key] for key in (
                "id","clinic_id","patient_id","created_by_doctor_id","invoice_number","idempotency_key","currency",
                "status","subtotal_paise","discount_paise","tax_paise","total_paise","paid_paise",
                "patient_visible","patient_note","due_date","issued_at","voided_at","created_at","updated_at"
            )), connection=connection)
            if not inserted:
                existing = self._execute(existing_sql, (clinic_id, idempotency_key), connection=connection)
                if not existing:
                    raise ValueError("Invoice idempotency conflict could not be reconciled")
                existing_invoice = self.get_invoice(existing[0]["id"], clinic_id, connection=connection)
                if not self._invoice_request_matches(existing_invoice, invoice, calculated_items):
                    raise ValueError("Invoice idempotency key was already used for a different invoice")
                return existing_invoice
            for item in calculated_items:
                item["invoice_id"] = invoice_id
                item_sql = (
                    "INSERT INTO dental_invoice_items(id,clinic_id,invoice_id,description,quantity_milli,unit_price_paise,tax_rate_bps,subtotal_paise,discount_paise,tax_paise,total_paise,sort_order,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    if self.database_url
                    else "INSERT INTO dental_invoice_items(id,clinic_id,invoice_id,description,quantity_milli,unit_price_paise,tax_rate_bps,subtotal_paise,discount_paise,tax_paise,total_paise,sort_order,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                )
                self._execute(item_sql, tuple(item[key] for key in (
                    "id","clinic_id","invoice_id","description","quantity_milli","unit_price_paise",
                    "tax_rate_bps","subtotal_paise","discount_paise","tax_paise","total_paise","sort_order","created_at"
                )), connection=connection)
            self.audit(doctor_id, "invoice.created", "invoice", invoice_id, clinic_id, connection)
        return self.get_invoice(invoice_id, clinic_id)

    def issue_invoice(
        self, invoice_id: str, clinic_id: str, doctor_id: str, due_date: str | None = None
    ) -> dict[str, Any] | None:
        now = _now()
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT status FROM dental_invoices WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT status FROM dental_invoices WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (invoice_id, clinic_id), connection=connection)
            if not rows:
                return None
            if rows[0]["status"] != "draft":
                raise ValueError("Only a draft invoice can be issued")
            update_sql = (
                "UPDATE dental_invoices SET status='issued',patient_visible=true,due_date=%s,issued_at=%s,updated_at=%s WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "UPDATE dental_invoices SET status='issued',patient_visible=1,due_date=?,issued_at=?,updated_at=? WHERE id=? AND clinic_id=?"
            )
            self._execute(update_sql, (due_date, now, now, invoice_id, clinic_id), connection=connection)
            self.audit(doctor_id, "invoice.issued", "invoice", invoice_id, clinic_id, connection)
        return self.get_invoice(invoice_id, clinic_id)

    def record_invoice_receipt(
        self, invoice_id: str, clinic_id: str, doctor_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        idempotency_key = data["idempotency_key"].strip()
        amount = int(data["amount_paise"])
        if amount <= 0:
            raise ValueError("Receipt amount must be greater than zero")
        with self.transaction() as connection:
            existing_sql = (
                "SELECT * FROM dental_receipts WHERE clinic_id=%s AND idempotency_key=%s"
                if self.database_url
                else "SELECT * FROM dental_receipts WHERE clinic_id=? AND idempotency_key=?"
            )
            existing = self._execute(existing_sql, (clinic_id, idempotency_key), connection=connection)
            if existing:
                receipt = existing[0]
                if (
                    receipt["invoice_id"] != invoice_id
                    or int(receipt["amount_paise"]) != amount
                    or receipt["method"] != data["method"]
                ):
                    raise ValueError("Idempotency key was already used for a different receipt")
                return receipt
            lock = " FOR UPDATE" if self.database_url else ""
            invoice_sql = (
                "SELECT * FROM dental_invoices WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT * FROM dental_invoices WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(invoice_sql, (invoice_id, clinic_id), connection=connection)
            if not rows:
                raise ValueError("Invoice not found in this workspace")
            invoice = rows[0]
            if invoice["status"] not in ("issued", "partially_paid", "overdue"):
                raise ValueError("Receipts can only be recorded against an issued invoice")
            balance = int(invoice["total_paise"]) - int(invoice["paid_paise"])
            if amount > balance:
                raise ValueError("Receipt amount exceeds the outstanding invoice balance")
            now = _now()
            receipt = {
                "id": _id(), "clinic_id": clinic_id, "invoice_id": invoice_id,
                "patient_id": invoice["patient_id"],
                "receipt_number": "RCPT-" + datetime.now(UTC).strftime("%Y%m") + "-" + uuid.uuid4().hex[:8].upper(),
                "amount_paise": amount, "method": data["method"],
                "reference": data.get("reference", "").strip(),
                "idempotency_key": idempotency_key, "paid_at": data.get("paid_at") or now,
                "created_by_doctor_id": doctor_id, "created_at": now,
            }
            receipt_sql = (
                "INSERT INTO dental_receipts(id,clinic_id,invoice_id,patient_id,receipt_number,amount_paise,method,reference,idempotency_key,paid_at,created_by_doctor_id,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (clinic_id,idempotency_key) DO NOTHING RETURNING id"
                if self.database_url
                else "INSERT INTO dental_receipts(id,clinic_id,invoice_id,patient_id,receipt_number,amount_paise,method,reference,idempotency_key,paid_at,created_by_doctor_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(clinic_id,idempotency_key) DO NOTHING RETURNING id"
            )
            inserted = self._execute(receipt_sql, tuple(receipt[key] for key in (
                "id","clinic_id","invoice_id","patient_id","receipt_number","amount_paise",
                "method","reference","idempotency_key","paid_at","created_by_doctor_id","created_at"
            )), connection=connection)
            if not inserted:
                duplicate = self._execute(existing_sql, (clinic_id, idempotency_key), connection=connection)
                if duplicate and duplicate[0]["invoice_id"] == invoice_id and int(duplicate[0]["amount_paise"]) == amount and duplicate[0]["method"] == data["method"]:
                    return duplicate[0]
                raise ValueError("Idempotency key was already used for a different receipt")
            paid = int(invoice["paid_paise"]) + amount
            status = "paid" if paid == int(invoice["total_paise"]) else "partially_paid"
            update_sql = (
                "UPDATE dental_invoices SET paid_paise=%s,status=%s,updated_at=%s WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "UPDATE dental_invoices SET paid_paise=?,status=?,updated_at=? WHERE id=? AND clinic_id=?"
            )
            self._execute(update_sql, (paid, status, now, invoice_id, clinic_id), connection=connection)
            self.audit(doctor_id, "invoice.receipt_recorded", "receipt", receipt["id"], clinic_id, connection)
        return receipt

    def void_invoice(self, invoice_id: str, clinic_id: str, doctor_id: str):
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT status FROM dental_invoices WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT status FROM dental_invoices WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (invoice_id, clinic_id), connection=connection)
            if not rows:
                return None
            if rows[0]["status"] not in ("draft", "issued", "overdue"):
                raise ValueError("Only an unpaid invoice can be voided")
            count_sql = (
                "SELECT COUNT(*) AS n FROM dental_receipts WHERE clinic_id=%s AND invoice_id=%s"
                if self.database_url
                else "SELECT COUNT(*) AS n FROM dental_receipts WHERE clinic_id=? AND invoice_id=?"
            )
            count = self._execute(count_sql, (clinic_id, invoice_id), connection=connection)
            if count and int(count[0]["n"]) > 0:
                raise ValueError("An invoice with receipts cannot be voided")
            now = _now()
            update_sql = (
                "UPDATE dental_invoices SET status='void',patient_visible=false,voided_at=%s,updated_at=%s WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "UPDATE dental_invoices SET status='void',patient_visible=0,voided_at=?,updated_at=? WHERE id=? AND clinic_id=?"
            )
            self._execute(update_sql, (now, now, invoice_id, clinic_id), connection=connection)
            self.audit(doctor_id, "invoice.voided", "invoice", invoice_id, clinic_id, connection)
        return self.get_invoice(invoice_id, clinic_id)

    def receipts(
        self, clinic_id: str, patient_id: str, patient_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        visibility = (
            " AND i.patient_visible=true AND i.status <> 'void'"
            if self.database_url
            else " AND i.patient_visible=1 AND i.status <> 'void'"
        ) if patient_visible_only else ""
        sql = (
            "SELECT r.id,r.clinic_id,r.invoice_id,r.patient_id,r.receipt_number,r.amount_paise,r.method,r.reference,r.paid_at,r.created_at "
            "FROM dental_receipts r JOIN dental_invoices i ON i.id=r.invoice_id AND i.clinic_id=r.clinic_id "
            "WHERE r.clinic_id=%s AND r.patient_id=%s" + visibility + " ORDER BY r.paid_at DESC"
            if self.database_url
            else "SELECT r.id,r.clinic_id,r.invoice_id,r.patient_id,r.receipt_number,r.amount_paise,r.method,r.reference,r.paid_at,r.created_at "
            "FROM dental_receipts r JOIN dental_invoices i ON i.id=r.invoice_id AND i.clinic_id=r.clinic_id "
            "WHERE r.clinic_id=? AND r.patient_id=?" + visibility + " ORDER BY r.paid_at DESC"
        )
        return self._execute(sql, (clinic_id, patient_id))

    def dashboard_summary(self, clinic_id: str) -> dict[str, int]:
        """Return clinic-scoped aggregate metrics without exposing patient records."""
        placeholder = "%s" if self.database_url else "?"

        patients = self._execute(
            f"SELECT COUNT(*) AS total FROM dental_patients WHERE clinic_id={placeholder}",
            (clinic_id,),
        )
        appointments = self._execute(
            f"SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN status='requested' THEN 1 ELSE 0 END),0) AS pending "
            f"FROM dental_appointments WHERE clinic_id={placeholder}",
            (clinic_id,),
        )
        treatments = self._execute(
            f"SELECT COUNT(*) AS total FROM dental_treatment_plans "
            f"WHERE clinic_id={placeholder} AND status IN ('proposed','accepted','in_progress')",
            (clinic_id,),
        )
        invoices = self._execute(
            f"SELECT COUNT(*) AS invoice_count, "
            "COALESCE(SUM(total_paise),0) AS invoiced_total_paise, "
            "COALESCE(SUM(paid_paise),0) AS collected_total_paise, "
            "COALESCE(SUM(CASE WHEN status IN ('issued','partially_paid','overdue') "
            "THEN total_paise-paid_paise ELSE 0 END),0) AS outstanding_balance_paise "
            f"FROM dental_invoices WHERE clinic_id={placeholder} AND status NOT IN ('draft','void')",
            (clinic_id,),
        )

        patient_row = patients[0] if patients else {}
        appointment_row = appointments[0] if appointments else {}
        treatment_row = treatments[0] if treatments else {}
        invoice_row = invoices[0] if invoices else {}
        return {
            "patient_count": int(patient_row.get("total") or 0),
            "appointment_count": int(appointment_row.get("total") or 0),
            "pending_appointment_requests": int(appointment_row.get("pending") or 0),
            "active_treatment_plan_count": int(treatment_row.get("total") or 0),
            "issued_invoice_count": int(invoice_row.get("invoice_count") or 0),
            "invoiced_total_paise": int(invoice_row.get("invoiced_total_paise") or 0),
            "collected_total_paise": int(invoice_row.get("collected_total_paise") or 0),
            "outstanding_balance_paise": int(invoice_row.get("outstanding_balance_paise") or 0),
        }

    def get_clinic(self, clinic_id: str) -> dict[str, Any] | None:
        rows = self._execute(
            "SELECT id,organisation_id,name FROM dental_clinics WHERE id=%s"
            if self.database_url
            else "SELECT id,organisation_id,name FROM dental_clinics WHERE id=?",
            (clinic_id,),
        )
        return rows[0] if rows else None

    def create_document(
        self, data: dict[str, Any], clinic_id: str, doctor_id: str
    ) -> dict[str, Any]:
        now = _now()
        size_bytes = int(data["size_bytes"])
        checksum_sha256 = str(data["checksum_sha256"]).lower()
        if size_bytes <= 0 or size_bytes > 10 * 1024 * 1024:
            raise ValueError("Document size is outside the allowed range")
        if len(checksum_sha256) != 64 or any(char not in "0123456789abcdef" for char in checksum_sha256):
            raise ValueError("Document SHA-256 checksum is invalid")
        upload_object_key = str(data["upload_object_key"])
        if not upload_object_key or len(upload_object_key) > 1024:
            raise ValueError("Document upload object key is invalid")
        document = {
            "id": data["id"],
            "organisation_id": data["organisation_id"],
            "clinic_id": clinic_id,
            "patient_id": data["patient_id"],
            "uploaded_by_doctor_id": doctor_id,
            "file_name": data["file_name"],
            "content_type": data["content_type"],
            "size_bytes": size_bytes,
            "checksum_sha256": checksum_sha256,
            "upload_object_key": upload_object_key,
            "object_key": None,
            "status": "pending_upload",
            "patient_visible": False,
            "reviewed_by_doctor_id": None,
            "reviewed_at": None,
            "created_at": now,
            "uploaded_at": None,
            "updated_at": now,
        }
        with self.transaction() as connection:
            patient_sql = (
                "SELECT id FROM dental_patients WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "SELECT id FROM dental_patients WHERE id=? AND clinic_id=?"
            )
            if not self._execute(patient_sql, (document["patient_id"], clinic_id), connection=connection):
                raise ValueError("Patient not found in this workspace")
            clinic_sql = (
                "SELECT organisation_id FROM dental_clinics WHERE id=%s"
                if self.database_url
                else "SELECT organisation_id FROM dental_clinics WHERE id=?"
            )
            clinic = self._execute(clinic_sql, (clinic_id,), connection=connection)
            if not clinic or clinic[0]["organisation_id"] != document["organisation_id"]:
                raise ValueError("Clinic organisation does not match the active workspace")
            insert_sql = (
                "INSERT INTO dental_documents(id,organisation_id,clinic_id,patient_id,uploaded_by_doctor_id,file_name,content_type,size_bytes,checksum_sha256,upload_object_key,object_key,status,patient_visible,reviewed_by_doctor_id,reviewed_at,created_at,uploaded_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                if self.database_url
                else "INSERT INTO dental_documents(id,organisation_id,clinic_id,patient_id,uploaded_by_doctor_id,file_name,content_type,size_bytes,checksum_sha256,upload_object_key,object_key,status,patient_visible,reviewed_by_doctor_id,reviewed_at,created_at,uploaded_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            )
            self._execute(insert_sql, tuple(document[key] for key in (
                "id","organisation_id","clinic_id","patient_id","uploaded_by_doctor_id","file_name",
                "content_type","size_bytes","checksum_sha256","upload_object_key","object_key","status","patient_visible",
                "reviewed_by_doctor_id","reviewed_at","created_at","uploaded_at","updated_at"
            )), connection=connection)
            self.audit(doctor_id, "document.upload_intent_created", "document", document["id"], clinic_id, connection)
        return document

    def get_document(
        self, document_id: str, clinic_id: str, patient_id: str | None = None,
        patient_visible_only: bool = False,
    ) -> dict[str, Any] | None:
        clauses = ["id=%s", "clinic_id=%s"] if self.database_url else ["id=?", "clinic_id=?"]
        params: list[Any] = [document_id, clinic_id]
        if patient_id is not None:
            clauses.append("patient_id=" + ("%s" if self.database_url else "?"))
            params.append(patient_id)
        if patient_visible_only:
            clauses.extend([
                "patient_visible=true" if self.database_url else "patient_visible=1",
                "status='available'",
                "reviewed_at IS NOT NULL",
            ])
        rows = self._execute(
            "SELECT * FROM dental_documents WHERE " + " AND ".join(clauses),
            tuple(params),
        )
        return rows[0] if rows else None

    def documents(
        self, clinic_id: str, patient_id: str, patient_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        visibility = (
            " AND patient_visible=true AND status='available' AND reviewed_at IS NOT NULL"
            if self.database_url
            else " AND patient_visible=1 AND status='available' AND reviewed_at IS NOT NULL"
        ) if patient_visible_only else ""
        sql = (
            "SELECT * FROM dental_documents WHERE clinic_id=%s AND patient_id=%s"
            + visibility + " ORDER BY created_at DESC"
            if self.database_url
            else "SELECT * FROM dental_documents WHERE clinic_id=? AND patient_id=?"
            + visibility + " ORDER BY created_at DESC"
        )
        return self._execute(sql, (clinic_id, patient_id))

    def claim_document_upload(
        self, document_id: str, clinic_id: str, doctor_id: str
    ) -> bool:
        """Atomically allow only one request to finalize a pending upload."""
        now = _now()
        with self.transaction() as connection:
            claim_sql = (
                "UPDATE dental_documents SET status='verifying',updated_at=%s "
                "WHERE id=%s AND clinic_id=%s AND status='pending_upload' RETURNING id"
                if self.database_url
                else "UPDATE dental_documents SET status='verifying',updated_at=? "
                "WHERE id=? AND clinic_id=? AND status='pending_upload' RETURNING id"
            )
            rows = self._execute(
                claim_sql, (now, document_id, clinic_id), connection=connection
            )
            if rows:
                self.audit(
                    doctor_id,
                    "document.upload_verification_started",
                    "document",
                    document_id,
                    clinic_id,
                    connection,
                )
            return bool(rows)

    def release_document_upload_claim(
        self, document_id: str, clinic_id: str, doctor_id: str
    ) -> bool:
        """Return a claimed upload to pending when a required scanner is unavailable."""
        now = _now()
        with self.transaction() as connection:
            release_sql = (
                "UPDATE dental_documents SET status='pending_upload',updated_at=%s "
                "WHERE id=%s AND clinic_id=%s AND status='verifying' RETURNING id"
                if self.database_url
                else "UPDATE dental_documents SET status='pending_upload',updated_at=? "
                "WHERE id=? AND clinic_id=? AND status='verifying' RETURNING id"
            )
            rows = self._execute(
                release_sql, (now, document_id, clinic_id), connection=connection
            )
            if rows:
                self.audit(
                    doctor_id,
                    "document.upload_verification_retryable",
                    "document",
                    document_id,
                    clinic_id,
                    connection,
                )
            return bool(rows)

    def mark_document_uploaded(
        self, document_id: str, clinic_id: str, doctor_id: str, final_object_key: str
    ):
        now = _now()
        if not final_object_key or len(final_object_key) > 1024:
            raise ValueError("Verified document object key is invalid")
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT status FROM dental_documents WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT status FROM dental_documents WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (document_id, clinic_id), connection=connection)
            if not rows:
                return None
            if rows[0]["status"] != "verifying":
                raise ValueError("Only a claimed document upload can be completed")
            update_sql = (
                "UPDATE dental_documents SET object_key=%s,status='available',uploaded_at=%s,updated_at=%s "
                "WHERE id=%s AND clinic_id=%s AND status='verifying'"
                if self.database_url
                else "UPDATE dental_documents SET object_key=?,status='available',uploaded_at=?,updated_at=? "
                "WHERE id=? AND clinic_id=? AND status='verifying'"
            )
            self._execute(
                update_sql,
                (final_object_key, now, now, document_id, clinic_id),
                connection=connection,
            )
            self.audit(doctor_id, "document.upload_completed", "document", document_id, clinic_id, connection)
        return self.get_document(document_id, clinic_id)

    def mark_document_rejected(
        self, document_id: str, clinic_id: str, doctor_id: str
    ):
        now = _now()
        with self.transaction() as connection:
            update_sql = (
                "UPDATE dental_documents SET status='rejected',updated_at=%s "
                "WHERE id=%s AND clinic_id=%s AND status IN ('pending_upload','verifying') RETURNING id"
                if self.database_url
                else "UPDATE dental_documents SET status='rejected',updated_at=? "
                "WHERE id=? AND clinic_id=? AND status IN ('pending_upload','verifying') RETURNING id"
            )
            rows = self._execute(
                update_sql, (now, document_id, clinic_id), connection=connection
            )
            if rows:
                self.audit(doctor_id, "document.upload_rejected", "document", document_id, clinic_id, connection)
        return self.get_document(document_id, clinic_id)

    def review_document(
        self, document_id: str, clinic_id: str, doctor_id: str
    ):
        """Record an explicit clinician review before a document can be shared."""
        now = _now()
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT status FROM dental_documents WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT status FROM dental_documents WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (document_id, clinic_id), connection=connection)
            if not rows:
                return None
            if rows[0]["status"] != "available":
                raise ValueError("Only an available document can be reviewed")
            update_sql = (
                "UPDATE dental_documents SET reviewed_by_doctor_id=%s,reviewed_at=%s,updated_at=%s "
                "WHERE id=%s AND clinic_id=%s AND status='available'"
                if self.database_url
                else "UPDATE dental_documents SET reviewed_by_doctor_id=?,reviewed_at=?,updated_at=? "
                "WHERE id=? AND clinic_id=? AND status='available'"
            )
            self._execute(
                update_sql,
                (doctor_id, now, now, document_id, clinic_id),
                connection=connection,
            )
            self.audit(doctor_id, "document.reviewed", "document", document_id, clinic_id, connection)
        return self.get_document(document_id, clinic_id)

    def set_document_visibility(
        self, document_id: str, clinic_id: str, doctor_id: str, visible: bool
    ):
        now = _now()
        with self.transaction() as connection:
            lock = " FOR UPDATE" if self.database_url else ""
            sql = (
                "SELECT status,reviewed_at FROM dental_documents WHERE id=%s AND clinic_id=%s" + lock
                if self.database_url
                else "SELECT status,reviewed_at FROM dental_documents WHERE id=? AND clinic_id=?"
            )
            rows = self._execute(sql, (document_id, clinic_id), connection=connection)
            if not rows:
                return None
            if rows[0]["status"] != "available":
                raise ValueError("Only an available document can be shared with the patient")
            if visible and not rows[0].get("reviewed_at"):
                raise DocumentReviewRequiredError("A doctor must review this document before patient sharing is enabled")
            update_sql = (
                "UPDATE dental_documents SET patient_visible=%s,updated_at=%s WHERE id=%s AND clinic_id=%s"
                if self.database_url
                else "UPDATE dental_documents SET patient_visible=?,updated_at=? WHERE id=? AND clinic_id=?"
            )
            self._execute(update_sql, (visible, now, document_id, clinic_id), connection=connection)
            self.audit(
                doctor_id,
                "document.shared" if visible else "document.unshared",
                "document",
                document_id,
                clinic_id,
                connection,
            )
        return self.get_document(document_id, clinic_id)
    def patients_by_user_id(self, user_id: str) -> list[dict[str, Any]]:
        """Return the clinic records explicitly linked to one authenticated patient account."""
        sql = (
            "SELECT p.id AS patient_id,p.name AS patient_name,p.clinic_id,"
            "c.name AS clinic_name,o.name AS organisation_name,p.created_at "
            "FROM dental_patients p "
            "JOIN dental_clinics c ON c.id=p.clinic_id "
            "LEFT JOIN dental_organisations o ON o.id=c.organisation_id "
            "WHERE p.user_id=%s ORDER BY p.created_at DESC,p.id"
            if self.database_url
            else
            "SELECT p.id AS patient_id,p.name AS patient_name,p.clinic_id,"
            "c.name AS clinic_name,o.name AS organisation_name,p.created_at "
            "FROM dental_patients p "
            "JOIN dental_clinics c ON c.id=p.clinic_id "
            "LEFT JOIN dental_organisations o ON o.id=c.organisation_id "
            "WHERE p.user_id=? ORDER BY p.created_at DESC,p.id"
        )
        return self._execute(sql, (user_id,))

    def patient_by_user_id(self, user_id: str, patient_id: str | None = None):
        if patient_id:
            sql = (
                "SELECT * FROM dental_patients WHERE user_id=%s AND id=%s"
                if self.database_url
                else "SELECT * FROM dental_patients WHERE user_id=? AND id=?"
            )
            rows = self._execute(sql, (user_id, patient_id))
        else:
            sql = (
                "SELECT * FROM dental_patients WHERE user_id=%s ORDER BY created_at DESC LIMIT 1"
                if self.database_url
                else "SELECT * FROM dental_patients WHERE user_id=? ORDER BY created_at DESC LIMIT 1"
            )
            rows = self._execute(sql, (user_id,))
        return rows[0] if rows else None

    def create_patient_invite(
        self, patient_id: str, clinic_id: str, ttl_seconds: int = 86400
    ) -> str:
        alphabet = string.ascii_uppercase + string.digits
        code = "".join(secrets.choice(alphabet) for _ in range(10))
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        sql = (
            "INSERT INTO dental_patient_invites(id,code_hash,clinic_id,patient_id,expires_at,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_patient_invites(id,code_hash,clinic_id,patient_id,expires_at,created_at) VALUES (?,?,?,?,?,?)"
        )
        self._execute(
            sql,
            (
                _id(),
                code_hash,
                clinic_id,
                patient_id,
                str(int(time.time()) + ttl_seconds),
                _now(),
            ),
        )
        return code

    def consume_patient_invite(self, code: str, user_id: str):
        """Consume an invite and bind its patient record atomically to one account."""
        code_hash = hashlib.sha256(code.strip().upper().encode()).hexdigest()
        lock = " FOR UPDATE" if self.database_url else ""

        class InviteLinkConflict(Exception):
            pass

        try:
            with self.transaction() as connection:
                if not self.database_url:
                    # Serialize SQLite invite linking before reading either row.
                    connection.execute("BEGIN IMMEDIATE")

                invite_sql = (
                    "SELECT * FROM dental_patient_invites WHERE code_hash=%s AND used_at IS NULL" + lock
                    if self.database_url
                    else "SELECT * FROM dental_patient_invites WHERE code_hash=? AND used_at IS NULL"
                )
                invites = self._execute(invite_sql, (code_hash,), connection=connection)
                if not invites or int(float(invites[0]["expires_at"])) < int(time.time()):
                    return None

                invite = invites[0]
                patient_sql = (
                    "SELECT * FROM dental_patients WHERE id=%s AND clinic_id=%s" + lock
                    if self.database_url
                    else "SELECT * FROM dental_patients WHERE id=? AND clinic_id=?"
                )
                patients = self._execute(
                    patient_sql,
                    (invite["patient_id"], invite["clinic_id"]),
                    connection=connection,
                )
                if not patients:
                    return None

                patient = patients[0]
                if patient.get("user_id") and patient["user_id"] != user_id:
                    return None

                consume_sql = (
                    "UPDATE dental_patient_invites SET used_at=%s,used_by=%s "
                    "WHERE id=%s AND used_at IS NULL RETURNING id"
                    if self.database_url
                    else "UPDATE dental_patient_invites SET used_at=?,used_by=? "
                    "WHERE id=? AND used_at IS NULL RETURNING id"
                )
                consumed = self._execute(
                    consume_sql, (_now(), user_id, invite["id"]), connection=connection
                )
                if not consumed:
                    return None

                link_sql = (
                    "UPDATE dental_patients SET user_id=%s "
                    "WHERE id=%s AND clinic_id=%s AND (user_id IS NULL OR user_id=%s) RETURNING id"
                    if self.database_url
                    else "UPDATE dental_patients SET user_id=? "
                    "WHERE id=? AND clinic_id=? AND (user_id IS NULL OR user_id=?) RETURNING id"
                )
                linked = self._execute(
                    link_sql,
                    (user_id, invite["patient_id"], invite["clinic_id"], user_id),
                    connection=connection,
                )
                if not linked:
                    # Roll back invite consumption as well as the attempted link.
                    raise InviteLinkConflict

                patient["user_id"] = user_id
                return patient
        except InviteLinkConflict:
            return None

    def ai_usage_count(
        self, actor_id: str | None, since_epoch: int, clinic_id: str | None = None
    ) -> int:
        if clinic_id:
            sql = (
                "SELECT COUNT(*) AS n FROM dental_ai_usage WHERE clinic_id=%s AND created_at >= %s"
                if self.database_url
                else "SELECT COUNT(*) AS n FROM dental_ai_usage WHERE clinic_id=? AND created_at >= ?"
            )
            params: tuple[Any, ...] = (clinic_id, _iso_from_epoch(since_epoch))
        else:
            sql = (
                "SELECT COUNT(*) AS n FROM dental_ai_usage WHERE actor_id=%s AND created_at >= %s"
                if self.database_url
                else "SELECT COUNT(*) AS n FROM dental_ai_usage WHERE actor_id=? AND created_at >= ?"
            )
            params = (actor_id, _iso_from_epoch(since_epoch))
        rows = self._execute(sql, params)
        return int(rows[0]["n"]) if rows else 0

    def record_ai_usage(
        self,
        actor_id: str,
        clinic_id: str,
        actor_type: str,
        endpoint: str,
        cache_hit: bool,
    ) -> None:
        sql = (
            "INSERT INTO dental_ai_usage(id,actor_id,clinic_id,actor_type,endpoint,cache_hit,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_ai_usage(id,actor_id,clinic_id,actor_type,endpoint,cache_hit,created_at) VALUES (?,?,?,?,?,?,?)"
        )
        self._execute(
            sql, (_id(), actor_id, clinic_id, actor_type, endpoint, cache_hit, _now())
        )

    def get_ai_cache(self, cache_key: str):
        sql = (
            "SELECT result_json,expires_at FROM dental_ai_cache WHERE cache_key=%s"
            if self.database_url
            else "SELECT result_json,expires_at FROM dental_ai_cache WHERE cache_key=?"
        )
        rows = self._execute(sql, (cache_key,))
        if not rows:
            return None
        if float(rows[0]["expires_at"]) < time.time():
            self._execute(
                "DELETE FROM dental_ai_cache WHERE cache_key=%s"
                if self.database_url
                else "DELETE FROM dental_ai_cache WHERE cache_key=?",
                (cache_key,),
            )
            return None
        try:
            return json.loads(rows[0]["result_json"])
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def put_ai_cache(
        self, cache_key: str, result: dict[str, Any], expires_at: int
    ) -> None:
        sql = (
            "INSERT INTO dental_ai_cache(cache_key,result_json,expires_at,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT (cache_key) DO UPDATE SET result_json=EXCLUDED.result_json, expires_at=EXCLUDED.expires_at"
            if self.database_url
            else "INSERT OR REPLACE INTO dental_ai_cache(cache_key,result_json,expires_at,created_at) VALUES (?,?,?,?)"
        )
        self._execute(
            sql,
            (
                cache_key,
                json.dumps(result, ensure_ascii=False),
                str(expires_at),
                _now(),
            ),
        )

    def save_ai_approval(
        self,
        clinic_id: str,
        patient_id: str,
        actor_id: str,
        capability: str,
        action: str,
        content: dict[str, Any],
    ) -> str:
        approval_id = _id()
        sql = (
            "INSERT INTO dental_ai_approvals(id,clinic_id,patient_id,actor_id,capability,action,content_json,approved_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_ai_approvals(id,clinic_id,patient_id,actor_id,capability,action,content_json,approved_at) VALUES (?,?,?,?,?,?,?,?)"
        )
        self._execute(
            sql,
            (
                approval_id,
                clinic_id,
                patient_id,
                actor_id,
                capability,
                action,
                json.dumps(content, ensure_ascii=False),
                _now(),
            ),
        )
        return approval_id

    def set_ai_consent(self, user_id: str, consent: bool) -> None:
        sql = (
            "INSERT INTO dental_patient_ai_consents(user_id,consent,updated_at) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET consent=EXCLUDED.consent, updated_at=EXCLUDED.updated_at"
            if self.database_url
            else "INSERT OR REPLACE INTO dental_patient_ai_consents(user_id,consent,updated_at) VALUES (?,?,?)"
        )
        self._execute(sql, (user_id, consent, _now()))

    def ai_consent(self, user_id: str) -> bool:
        sql = (
            "SELECT consent FROM dental_patient_ai_consents WHERE user_id=%s"
            if self.database_url
            else "SELECT consent FROM dental_patient_ai_consents WHERE user_id=?"
        )
        rows = self._execute(sql, (user_id,))
        return bool(rows and rows[0]["consent"])

    def save_patient_ai_message(
        self,
        user_id: str,
        patient_id: str | None,
        role: str,
        content: str,
        clinic_id: str,
    ) -> None:
        sql = (
            "INSERT INTO dental_patient_ai_messages(id,user_id,patient_id,clinic_id,role,content,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_patient_ai_messages(id,user_id,patient_id,clinic_id,role,content,created_at) VALUES (?,?,?,?,?,?,?)"
        )
        self._execute(
            sql, (_id(), user_id, patient_id, clinic_id, role, content, _now())
        )

    def patient_ai_messages(self, user_id: str, patient_id: str | None = None):
        sql = (
            "SELECT id,role,content,created_at FROM dental_patient_ai_messages WHERE user_id=%s"
            if self.database_url
            else "SELECT id,role,content,created_at FROM dental_patient_ai_messages WHERE user_id=?"
        )
        params: tuple[Any, ...] = (user_id,)
        if patient_id:
            sql += " AND patient_id=%s" if self.database_url else " AND patient_id=?"
            params += (patient_id,)
        sql += " ORDER BY created_at ASC"
        return self._execute(sql, params)

    def delete_patient_ai_messages(self, user_id: str) -> None:
        sql = (
            "DELETE FROM dental_patient_ai_messages WHERE user_id=%s"
            if self.database_url
            else "DELETE FROM dental_patient_ai_messages WHERE user_id=?"
        )
        self._execute(sql, (user_id,))

    def save_patient_device(
        self, user_id: str, patient_id: str | None, clinic_id: str, token: str
    ) -> None:
        if self.database_url:
            sql = "INSERT INTO dental_patient_devices(id,user_id,patient_id,clinic_id,expo_push_token,created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (expo_push_token) DO UPDATE SET user_id=EXCLUDED.user_id, patient_id=EXCLUDED.patient_id, clinic_id=EXCLUDED.clinic_id"
        else:
            sql = "INSERT OR REPLACE INTO dental_patient_devices(id,user_id,patient_id,clinic_id,expo_push_token,created_at) VALUES (?,?,?,?,?,?)"
        self._execute(sql, (_id(), user_id, patient_id, clinic_id, token, _now()))

    def patient_devices(self, user_id: str) -> list[str]:
        sql = (
            "SELECT expo_push_token FROM dental_patient_devices WHERE user_id=%s"
            if self.database_url
            else "SELECT expo_push_token FROM dental_patient_devices WHERE user_id=?"
        )
        return [r["expo_push_token"] for r in self._execute(sql, (user_id,))]


def _iso_from_epoch(value: int) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


_store: DentalStore | None = None
_store_lock = Lock()


def get_dental_store() -> DentalStore:
    """Initialize the persistence store once, even when requests arrive concurrently.

    Store construction can run Alembic migrations when DENTAL_AUTO_MIGRATE is
    enabled. Alembic uses process-global context state, so concurrent first
    requests must not attempt to initialize/migrate the store simultaneously.
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = DentalStore()
    return _store
