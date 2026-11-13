from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from universal_intelligence_engine import dental_documents
from universal_intelligence_engine.dental_document_cleanup import (
    cleanup_expired_pending_uploads,
)
from universal_intelligence_engine.dental_store import DentalStore


class FakeS3:
    def __init__(self):
        self.objects: set[str] = set()
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()

    def delete_object(self, Bucket: str, Key: str):
        if Key in self.fail_keys:
            raise OSError("synthetic storage deletion failure")
        self.deleted.append(Key)
        self.objects.discard(Key)
        return {"DeleteMarker": True}


@pytest.fixture
def cleanup_context(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "document-cleanup.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    organisation_id = "org-cleanup"
    clinic_id = "clinic-cleanup"
    store.create_organisation(
        {"id": organisation_id, "name": "Synthetic Cleanup Group", "organisation_type": "group"}
    )
    store.create_clinic(
        {"id": clinic_id, "organisation_id": organisation_id, "name": "Synthetic Cleanup Clinic", "clinic_type": "clinic"}
    )
    patient = store.create_patient(
        {"name": "Synthetic Cleanup Patient", "phone": "9000000099", "age": "30"},
        clinic_id,
    )
    return store, organisation_id, clinic_id, patient


def _create_document(
    store: DentalStore,
    organisation_id: str,
    clinic_id: str,
    patient: dict,
    *,
    status: str = "pending_upload",
    updated_at: datetime,
    unsafe_key: bool = False,
):
    document_id = uuid.uuid4().hex
    key = (
        f"pending/organisations/{organisation_id}/clinics/{clinic_id}/"
        f"patients/{patient['id']}/{document_id}/scan.pdf"
    )
    if unsafe_key:
        key = f"verified/organisations/{organisation_id}/clinics/{clinic_id}/scan.pdf"
    document = store.create_document(
        {
            "id": document_id,
            "organisation_id": organisation_id,
            "patient_id": patient["id"],
            "file_name": "scan.pdf",
            "content_type": "application/pdf",
            "size_bytes": 16,
            "checksum_sha256": "a" * 64,
            "upload_object_key": key,
        },
        clinic_id,
        "doctor-cleanup-test",
    )
    if status == "verifying":
        assert store.claim_document_upload(document_id, clinic_id, "doctor-cleanup-test")
    elif status == "rejected":
        store.mark_document_rejected(document_id, clinic_id, "doctor-cleanup-test")
    elif status == "available":
        assert store.claim_document_upload(document_id, clinic_id, "doctor-cleanup-test")
        store.mark_document_uploaded(
            document_id,
            clinic_id,
            "doctor-cleanup-test",
            f"verified/organisations/{organisation_id}/clinics/{clinic_id}/"
            f"patients/{patient['id']}/{document_id}/scan.pdf",
        )
    elif status != "pending_upload":
        raise ValueError("Unsupported synthetic document status")

    store._execute(
        "UPDATE dental_documents SET updated_at=? WHERE id=?",
        (updated_at.astimezone(UTC).isoformat(), document_id),
    )
    return store.get_document(document_id, clinic_id), key


def test_cleanup_dry_run_is_read_only(cleanup_context):
    store, organisation_id, clinic_id, patient = cleanup_context
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    old = now - timedelta(hours=30)
    document, key = _create_document(
        store, organisation_id, clinic_id, patient, updated_at=old
    )
    s3 = FakeS3()
    s3.objects.add(key)

    result = cleanup_expired_pending_uploads(
        store, s3, "private-test-bucket", now=now, dry_run=True
    )

    assert result == {
        "dry_run": True,
        "candidate_count": 1,
        "deleted_count": 0,
        "delete_failures": 0,
        "unsafe_keys": 0,
        "min_age_hours": 24,
    }
    current = store.get_document(document["id"], clinic_id)
    assert current["status"] == "pending_upload"
    assert current["upload_object_key"] == key
    assert key in s3.objects
    assert s3.deleted == []


def test_apply_deletes_only_old_quarantine_objects_and_never_verified_objects(cleanup_context):
    store, organisation_id, clinic_id, patient = cleanup_context
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    old = now - timedelta(hours=30)
    recent = now - timedelta(hours=2)
    pending, pending_key = _create_document(
        store, organisation_id, clinic_id, patient, updated_at=old
    )
    verifying, verifying_key = _create_document(
        store, organisation_id, clinic_id, patient, status="verifying", updated_at=old
    )
    rejected, rejected_key = _create_document(
        store, organisation_id, clinic_id, patient, status="rejected", updated_at=old
    )
    recent_pending, recent_key = _create_document(
        store, organisation_id, clinic_id, patient, updated_at=recent
    )
    available, verified_key = _create_document(
        store, organisation_id, clinic_id, patient, status="available", updated_at=old
    )
    s3 = FakeS3()
    s3.objects.update({pending_key, verifying_key, rejected_key, recent_key, verified_key})

    result = cleanup_expired_pending_uploads(
        store, s3, "private-test-bucket", now=now, dry_run=False
    )

    assert result == {
        "dry_run": False,
        "candidate_count": 3,
        "deleted_count": 3,
        "delete_failures": 0,
        "unsafe_keys": 0,
        "min_age_hours": 24,
    }
    for document in (pending, verifying, rejected):
        current = store.get_document(document["id"], clinic_id)
        assert current["status"] == "rejected"
        assert current["upload_object_key"] is None
    assert store.get_document(recent_pending["id"], clinic_id)["status"] == "pending_upload"
    available_after = store.get_document(available["id"], clinic_id)
    assert available_after["status"] == "available"
    assert available_after["object_key"] == verified_key
    assert verified_key in s3.objects
    assert set(s3.deleted) == {pending_key, verifying_key, rejected_key}
    for document in (pending, verifying):
        actions = {
            row["action"]
            for row in store._execute(
                "SELECT action FROM dental_audit_events WHERE resource_id=?",
                (document["id"],),
            )
        }
        assert "document.pending_upload_expired" in actions
        assert "document.pending_upload_object_cleaned" in actions


def test_cleanup_never_deletes_a_key_outside_exact_pending_document_prefix(cleanup_context):
    store, organisation_id, clinic_id, patient = cleanup_context
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    old = now - timedelta(hours=30)
    document, unsafe_key = _create_document(
        store, organisation_id, clinic_id, patient, updated_at=old, unsafe_key=True
    )
    s3 = FakeS3()
    s3.objects.add(unsafe_key)

    result = cleanup_expired_pending_uploads(
        store, s3, "private-test-bucket", now=now, dry_run=False
    )

    current = store.get_document(document["id"], clinic_id)
    assert result["candidate_count"] == 1
    assert result["unsafe_keys"] == 1
    assert result["deleted_count"] == 0
    assert current["status"] == "rejected"
    assert current["upload_object_key"] == unsafe_key
    assert unsafe_key in s3.objects
    assert s3.deleted == []


def test_failed_object_delete_remains_retryable(cleanup_context):
    store, organisation_id, clinic_id, patient = cleanup_context
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    old = now - timedelta(hours=30)
    document, key = _create_document(
        store, organisation_id, clinic_id, patient, updated_at=old
    )
    s3 = FakeS3()
    s3.objects.add(key)
    s3.fail_keys.add(key)

    failed = cleanup_expired_pending_uploads(
        store, s3, "private-test-bucket", now=now, dry_run=False
    )
    current = store.get_document(document["id"], clinic_id)
    assert failed["delete_failures"] == 1
    assert current["status"] == "rejected"
    assert current["upload_object_key"] == key

    s3.fail_keys.clear()
    retried = cleanup_expired_pending_uploads(
        store, s3, "private-test-bucket", now=now, dry_run=False
    )
    current = store.get_document(document["id"], clinic_id)
    assert retried["candidate_count"] == 1
    assert retried["deleted_count"] == 1
    assert current["upload_object_key"] is None
    assert key not in s3.objects


def test_cleanup_rejects_unsafe_age_and_batch_limits(cleanup_context):
    store, _organisation_id, _clinic_id, _patient = cleanup_context
    with pytest.raises(ValueError):
        cleanup_expired_pending_uploads(
            store, FakeS3(), "private-test-bucket", older_than_hours=1
        )
    with pytest.raises(ValueError):
        cleanup_expired_pending_uploads(
            store, FakeS3(), "private-test-bucket", limit=501
        )


def test_cleanup_cli_can_read_private_storage_before_lifecycle_attestation(monkeypatch):
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_ENDPOINT", "https://storage.example")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_REGION", "ap-southeast-1")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_BUCKET", "oralyn-private-documents")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID", "synthetic-access-key")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY", "synthetic-secret-key")
    monkeypatch.setenv("DENTAL_DOCUMENTS_STORAGE_PRIVATE", "true")
    monkeypatch.setenv("DENTAL_DOCUMENTS_LIFECYCLE_CLEANUP_VERIFIED", "false")

    config = dental_documents._storage_config(require_lifecycle=False)
    assert config["bucket"] == "oralyn-private-documents"
    with pytest.raises(HTTPException) as error:
        dental_documents._storage_config()
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "DOCUMENT_STORAGE_LIFECYCLE_NOT_VERIFIED"
