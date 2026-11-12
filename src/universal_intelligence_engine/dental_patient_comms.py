"""Tenant-scoped clinical notes and secure patient/clinic communication.

Clinical notes are private by default and become patient-visible only after an
explicit doctor action. Messages are shared only between the authenticated
clinic workspace and the linked patient record. No AI or clinical decision
support is exposed to patients here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dental_ai import require_patient
from .dental_auth import require_doctor
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-clinical-notes-messaging"])


class ClinicalNoteCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    content: str = Field(min_length=1, max_length=10000)


class ClinicalNoteUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    patient_visible: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)


class MessageCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


def _envelope(result):
    return {"result": result, "confidence": 1.0, "sources": [], "request_id": str(uuid.uuid4())}


def _patient_for_actor(actor: dict, patient_id: str | None = None) -> dict:
    store = get_dental_store()
    if patient_id:
        patient = store.patient_by_user_id(actor["user_id"], patient_id)
        if not patient:
            raise HTTPException(
                403,
                detail={
                    "code": "PATIENT_IDENTITY_MISMATCH",
                    "message": "The selected clinic record is not linked to this patient account.",
                },
            )
        return patient
    linked = store.patients_by_user_id(actor["user_id"])
    if len(linked) > 1:
        raise HTTPException(
            409,
            detail={
                "code": "PATIENT_RECORD_SELECTION_REQUIRED",
                "message": "Choose the clinic record you want to view.",
            },
        )
    if not linked:
        raise HTTPException(
            404,
            detail={
                "code": "PATIENT_RECORD_NOT_LINKED",
                "message": "No clinic record is linked to this patient account.",
            },
        )
    patient = store.patient_by_user_id(actor["user_id"], linked[0]["patient_id"])
    if not patient:
        raise HTTPException(404, detail={"code": "PATIENT_RECORD_NOT_LINKED", "message": "No clinic record is linked to this patient account."})
    return patient


def _note_public(row: dict, patient_view: bool = False) -> dict:
    result = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "version": int(row["version"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if not patient_view:
        result["patient_visible"] = bool(row["patient_visible"])
    return result


def _message_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "sender_type": row["sender_type"],
        "body": row["body"],
        "created_at": row["created_at"],
    }


def _query(store, postgres_sql: str, sqlite_sql: str, params: tuple, connection=None):
    return store._execute(postgres_sql if store.database_url else sqlite_sql, params, connection=connection)


def _messages_for(store, clinic_id: str, patient_id: str) -> list[dict]:
    rows = _query(
        store,
        "SELECT id,sender_type,body,created_at FROM dental_patient_messages WHERE clinic_id=%s AND patient_id=%s ORDER BY created_at,id",
        "SELECT id,sender_type,body,created_at FROM dental_patient_messages WHERE clinic_id=? AND patient_id=? ORDER BY created_at,id",
        (clinic_id, patient_id),
    )
    return [_message_public(row) for row in rows]


def _create_message(store, clinic_id: str, patient_id: str, sender_type: str, sender_id: str, body: str) -> dict:
    clean = body.strip()
    if not clean:
        raise HTTPException(400, detail={"code": "MESSAGE_EMPTY", "message": "Write a message before sending."})
    if len(clean) > 4000:
        raise HTTPException(400, detail={"code": "MESSAGE_TOO_LONG", "message": "Messages are limited to 4,000 characters."})
    item = {
        "id": _id(),
        "clinic_id": clinic_id,
        "patient_id": patient_id,
        "sender_type": sender_type,
        "sender_id": sender_id,
        "body": clean,
        "created_at": _now(),
    }
    with store.transaction() as connection:
        _query(
            store,
            "INSERT INTO dental_patient_messages(id,clinic_id,patient_id,sender_type,sender_id,body,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            "INSERT INTO dental_patient_messages(id,clinic_id,patient_id,sender_type,sender_id,body,created_at) VALUES (?,?,?,?,?,?,?)",
            tuple(item[key] for key in ("id", "clinic_id", "patient_id", "sender_type", "sender_id", "body", "created_at")),
            connection=connection,
        )
        store.audit(sender_id, "patient_message.created", "patient_message", item["id"], clinic_id, connection)
    return _message_public(item)


@router.get("/doctor/patients/{patient_id}/clinical-notes")
def list_doctor_clinical_notes(patient_id: str, actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace."})
    rows = _query(
        store,
        "SELECT * FROM dental_clinical_notes WHERE clinic_id=%s AND patient_id=%s ORDER BY created_at DESC,id DESC",
        "SELECT * FROM dental_clinical_notes WHERE clinic_id=? AND patient_id=? ORDER BY created_at DESC,id DESC",
        (actor["clinic_id"], patient_id),
    )
    return _envelope([_note_public(row) for row in rows])


@router.post("/doctor/patients/{patient_id}/clinical-notes")
def create_doctor_clinical_note(
    patient_id: str,
    body: ClinicalNoteCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace."})
    now = _now()
    item = {
        "id": _id(),
        "clinic_id": actor["clinic_id"],
        "patient_id": patient_id,
        "created_by_doctor_id": actor["id"],
        "title": body.title.strip(),
        "content": body.content.strip(),
        "patient_visible": False,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    with store.transaction() as connection:
        _query(
            store,
            "INSERT INTO dental_clinical_notes(id,clinic_id,patient_id,created_by_doctor_id,title,content,patient_visible,version,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            "INSERT INTO dental_clinical_notes(id,clinic_id,patient_id,created_by_doctor_id,title,content,patient_visible,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            tuple(item[key] for key in ("id", "clinic_id", "patient_id", "created_by_doctor_id", "title", "content", "patient_visible", "version", "created_at", "updated_at")),
            connection=connection,
        )
        store.audit(actor["id"], "clinical_note.created", "clinical_note", item["id"], actor["clinic_id"], connection)
    return _envelope(_note_public(item))


@router.patch("/doctor/clinical-notes/{note_id}")
def update_doctor_clinical_note(
    note_id: str,
    body: ClinicalNoteUpdateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    changes = body.model_dump(exclude_unset=True)
    expected_version = changes.pop("expected_version", None)
    if not changes:
        raise HTTPException(400, detail={"code": "CLINICAL_NOTE_NO_CHANGES", "message": "Provide at least one field to update."})
    store = get_dental_store()
    lock = " FOR UPDATE" if store.database_url else ""
    select_sql = (
        "SELECT * FROM dental_clinical_notes WHERE id=%s AND clinic_id=%s" + lock
        if store.database_url
        else "SELECT * FROM dental_clinical_notes WHERE id=? AND clinic_id=?"
    )
    with store.transaction() as connection:
        rows = store._execute(select_sql, (note_id, actor["clinic_id"]), connection=connection)
        if not rows:
            raise HTTPException(404, detail={"code": "CLINICAL_NOTE_NOT_FOUND", "message": "Clinical note not found in this workspace."})
        current = rows[0]
        if expected_version is not None and int(current["version"]) != expected_version:
            raise HTTPException(409, detail={"code": "CLINICAL_NOTE_VERSION_CONFLICT", "message": "This note changed elsewhere. Refresh before saving."})
        title = changes.get("title", current["title"])
        content = changes.get("content", current["content"])
        patient_visible = changes.get("patient_visible", current["patient_visible"])
        if title is None or content is None or not str(title).strip() or not str(content).strip():
            raise HTTPException(400, detail={"code": "CLINICAL_NOTE_INVALID", "message": "A note title and content are required."})
        version = int(current["version"]) + 1
        now = _now()
        _query(
            store,
            "UPDATE dental_clinical_notes SET title=%s,content=%s,patient_visible=%s,version=%s,updated_at=%s WHERE id=%s AND clinic_id=%s",
            "UPDATE dental_clinical_notes SET title=?,content=?,patient_visible=?,version=?,updated_at=? WHERE id=? AND clinic_id=?",
            (str(title).strip(), str(content).strip(), patient_visible, version, now, note_id, actor["clinic_id"]),
            connection=connection,
        )
        store.audit(actor["id"], "clinical_note.updated", "clinical_note", note_id, actor["clinic_id"], connection)
        updated = {**current, "title": str(title).strip(), "content": str(content).strip(), "patient_visible": patient_visible, "version": version, "updated_at": now}
    return _envelope(_note_public(updated))


@router.get("/patient/clinical-notes")
def list_patient_clinical_notes(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    store = get_dental_store()
    rows = _query(
        store,
        "SELECT * FROM dental_clinical_notes WHERE clinic_id=%s AND patient_id=%s AND patient_visible=true ORDER BY created_at DESC,id DESC",
        "SELECT * FROM dental_clinical_notes WHERE clinic_id=? AND patient_id=? AND patient_visible=1 ORDER BY created_at DESC,id DESC",
        (patient["clinic_id"], patient["id"]),
    )
    return _envelope([_note_public(row, patient_view=True) for row in rows])


@router.get("/doctor/patients/{patient_id}/messages")
def list_doctor_patient_messages(patient_id: str, actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace."})
    return _envelope(_messages_for(store, actor["clinic_id"], patient_id))


@router.post("/doctor/patients/{patient_id}/messages")
def send_doctor_patient_message(
    patient_id: str,
    body: MessageCreateIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace."})
    return _envelope(_create_message(store, actor["clinic_id"], patient_id, "doctor", actor["id"], body.body))


@router.get("/patient/messages")
def list_patient_messages(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    return _envelope(_messages_for(get_dental_store(), patient["clinic_id"], patient["id"]))


@router.post("/patient/messages")
def send_patient_message(
    body: MessageCreateIn,
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    return _envelope(_create_message(
        get_dental_store(),
        patient["clinic_id"],
        patient["id"],
        "patient",
        actor["user_id"],
        body.body,
    ))
