"""Dental persistence backend.

PostgreSQL is the production backend when DATABASE_URL is set. SQLite remains
supported for local development/tests. Database structure is owned by Alembic;
this module never creates tables directly.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
import secrets
import sqlite3
import string
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


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
            "WHERE m.doctor_id=%s AND m.active=true AND c.active=true AND o.active=true "
            "ORDER BY o.name"
            if self.database_url
            else "SELECT DISTINCT o.id, o.name, o.organisation_type, o.active, o.created_at "
            "FROM dental_organisations o "
            "JOIN dental_clinics c ON c.organisation_id=o.id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "WHERE m.doctor_id=? AND m.active=1 AND c.active=1 AND o.active=1 "
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
            "WHERE m.doctor_id=%s AND m.active=true AND c.active=true AND o.active=true "
            "ORDER BY o.name, c.name"
            if self.database_url
            else "SELECT c.id, c.id AS clinic_id, c.organisation_id, c.name, "
            "c.clinic_type, c.parent_clinic_id, c.active, c.created_at, "
            "o.name AS organisation_name, o.organisation_type, m.role "
            "FROM dental_clinics c "
            "JOIN dental_organisations o ON o.id=c.organisation_id "
            "JOIN dental_doctor_memberships m ON m.clinic_id=c.id "
            "WHERE m.doctor_id=? AND m.active=1 AND c.active=1 AND o.active=1 "
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
            "WHERE m.clinic_id=c.id AND m.active=true AND d.active=true "
            "AND home.organisation_id=c.organisation_id) AS active_doctors "
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
            "WHERE m.clinic_id=c.id AND m.active=1 AND d.active=1 "
            "AND home.organisation_id=c.organisation_id) AS active_doctors "
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
        self, doctor_id: str, clinic_id: str, active: bool
    ) -> None:
        sql = (
            "UPDATE dental_doctor_memberships SET active=%s "
            "WHERE doctor_id=%s AND clinic_id=%s"
            if self.database_url
            else "UPDATE dental_doctor_memberships SET active=? "
            "WHERE doctor_id=? AND clinic_id=?"
        )
        self._execute(sql, (active, doctor_id, clinic_id))

    def create_session(self, doctor_id: str, token_hash: str, expires_at: int):
        self._execute(
            "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (%s,%s,%s,%s)"
            if self.database_url
            else "INSERT INTO dental_sessions(token_hash,doctor_id,expires_at,revoked) VALUES (?,?,?,?)",
            (token_hash, doctor_id, str(expires_at), False),
        )

    def get_session(self, token_hash: str):
        rows = self._execute(
            "SELECT * FROM dental_sessions WHERE token_hash=%s"
            if self.database_url
            else "SELECT * FROM dental_sessions WHERE token_hash=?",
            (token_hash,),
        )
        return rows[0] if rows else None

    def revoke_session(self, token_hash: str) -> None:
        self._execute(
            "UPDATE dental_sessions SET revoked=true WHERE token_hash=%s"
            if self.database_url
            else "UPDATE dental_sessions SET revoked=1 WHERE token_hash=?",
            (token_hash,),
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
        code_hash = hashlib.sha256(code.strip().upper().encode()).hexdigest()
        sql = (
            "SELECT * FROM dental_patient_invites WHERE code_hash=%s AND used_at IS NULL"
            if self.database_url
            else "SELECT * FROM dental_patient_invites WHERE code_hash=? AND used_at IS NULL"
        )
        rows = self._execute(sql, (code_hash,))
        if not rows or int(float(rows[0]["expires_at"])) < int(time.time()):
            return None
        invite = rows[0]
        patient = self.get_patient(invite["patient_id"], invite["clinic_id"])
        if not patient:
            return None
        if patient.get("user_id") and patient["user_id"] != user_id:
            return None
        sql = (
            "UPDATE dental_patient_invites SET used_at=%s,used_by=%s WHERE id=%s AND used_at IS NULL RETURNING id"
            if self.database_url
            else "UPDATE dental_patient_invites SET used_at=?,used_by=? WHERE id=? AND used_at IS NULL RETURNING id"
        )
        consumed = self._execute(sql, (_now(), user_id, invite["id"]))
        if not consumed:
            return None
        self.link_patient_user(patient["id"], user_id)
        return patient

    def link_patient_user(self, patient_id: str, user_id: str) -> None:
        sql = (
            "UPDATE dental_patients SET user_id=%s WHERE id=%s"
            if self.database_url
            else "UPDATE dental_patients SET user_id=? WHERE id=?"
        )
        self._execute(sql, (user_id, patient_id))

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
