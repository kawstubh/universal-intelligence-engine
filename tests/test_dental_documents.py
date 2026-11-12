from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_documents
from universal_intelligence_engine.dental_document_scanner import (
    DocumentMalwareDetected,
    DocumentScannerNotConfigured,
    DocumentScannerUnavailable,
)
from universal_intelligence_engine.dental_store import DentalStore


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.presign_calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn, HttpMethod=None):
        self.presign_calls.append(
            {"operation": operation, "params": Params, "expires": ExpiresIn, "method": HttpMethod}
        )
        return f"https://private-storage.example/{Params['Key']}?expires={ExpiresIn}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("object not uploaded")
        return {key: value for key, value in self.objects[Key].items() if key != "Body"}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("object not uploaded")
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        payload = Body.read() if hasattr(Body, "read") else Body
        if hasattr(Body, "seek"):
            Body.seek(0)
        self.objects[Key] = {
            "ContentLength": len(payload),
            "ContentType": ContentType,
            "Metadata": dict(Metadata),
            "Body": payload,
        }
        return {"ETag": '"verified-test-object"'}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {"DeleteMarker": True}


@pytest.fixture
def document_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "documents.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    store.create_organisation({"id": "org-a", "name": "Clinic Group", "organisation_type": "group"})
    store.create_organisation({"id": "org-b", "name": "Other Clinic", "organisation_type": "clinic"})
    store.create_clinic({"id": "clinic-a", "organisation_id": "org-a", "name": "Main Clinic", "clinic_type": "clinic"})
    store.create_clinic({"id": "clinic-b", "organisation_id": "org-b", "name": "Other Clinic", "clinic_type": "clinic"})
    patient_a = store.create_patient({"name": "Synthetic Patient A", "phone": "9000000001", "age": "35"}, "clinic-a")
    patient_b = store.create_patient({"name": "Synthetic Patient B", "phone": "9000000002", "age": "29"}, "clinic-b")
    store._execute("UPDATE dental_patients SET user_id=? WHERE id=?", ("supabase-user-a", patient_a["id"]))
    store._execute("UPDATE dental_patients SET user_id=? WHERE id=?", ("supabase-user-b", patient_b["id"]))

    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    patient_actor = {"user_id": "supabase-user-a"}
    fake_s3 = FakeS3Client()
    config = {
        "endpoint": "https://private-storage.example",
        "bucket": "private-test-bucket",
        "access_key": "test-access-key",
        "secret_key": "test-secret-key",
        "region": "ap-southeast-1",
        "upload_ttl": 300,
        "download_ttl": 120,
    }
    monkeypatch.setattr(dental_documents, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_documents, "_storage_config", lambda: config)
    monkeypatch.setattr(dental_documents, "_get_s3_client", lambda _config: fake_s3)
    monkeypatch.setattr(
        dental_documents,
        "scan_document_bytes",
        lambda _payload: {"engine": "clamav", "status": "clean"},
    )

    app = FastAPI()
    app.include_router(dental_documents.router)
    app.dependency_overrides[dental_documents.require_doctor] = lambda: actor
    app.dependency_overrides[dental_documents.require_patient] = lambda: patient_actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b, fake_s3


def _upload_intent(
    client: TestClient,
    patient_id: str,
    content: bytes = b"%PDF-1.4\n% synthetic dental document\n%%EOF\n",
):
    checksum = hashlib.sha256(content).hexdigest()
    response = client.post(
        f"/v1/dental/doctor/patients/{patient_id}/documents/upload-intent",
        json={
            "file_name": "scan-16.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(content),
            "checksum_sha256": checksum,
        },
    )
    return response, content, checksum


def test_private_document_upload_verify_share_and_patient_download(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    assert intent.status_code == 200, intent.text
    result = intent.json()["result"]
    document_id = result["document"]["id"]
    assert result["document"]["status"] == "pending_upload"
    assert result["document"]["patient_visible"] is False
    assert result["expires_in_seconds"] == 300
    assert result["required_headers"]["x-amz-meta-sha256"] == checksum
    assert "object_key" not in result["document"]

    stored = store.get_document(document_id, "clinic-a")
    assert stored["upload_object_key"].startswith("pending/organisations/org-a/clinics/clinic-a/patients/")
    assert stored["object_key"] is None
    assert stored["size_bytes"] == len(content)

    incomplete = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"]["code"] == "DOCUMENT_UPLOAD_INCOMPLETE"

    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    completed = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["result"]["status"] == "available"
    verified = store.get_document(document_id, "clinic-a")
    assert verified["object_key"].startswith("verified/organisations/org-a/clinics/clinic-a/patients/")
    assert verified["object_key"] != verified["upload_object_key"]
    assert fake_s3.objects[verified["object_key"]]["Body"] == content
    assert verified["upload_object_key"] not in fake_s3.objects

    assert verified["reviewed_at"] is None
    assert verified["reviewed_by_doctor_id"] is None
    scan_actions = [
        row["action"]
        for row in store._execute(
            "SELECT action FROM dental_audit_events WHERE resource_id=?",
            (document_id,),
        )
    ]
    assert "document.malware_scan_clean" in scan_actions

    premature_share = client.patch(
        f"/v1/dental/doctor/documents/{document_id}",
        json={"patient_visible": True},
    )
    assert premature_share.status_code == 409
    assert premature_share.json()["detail"]["code"] == "DOCUMENT_REVIEW_REQUIRED"
    assert client.get("/v1/dental/patient/documents").json()["result"] == []

    reviewed = client.post(f"/v1/dental/doctor/documents/{document_id}/review")
    assert reviewed.status_code == 200, reviewed.text
    review_result = reviewed.json()["result"]
    assert review_result["reviewed_by_doctor_id"] == "doctor-a"
    assert review_result["reviewed_at"]
    assert review_result["patient_visible"] is False
    assert client.get("/v1/dental/patient/documents").json()["result"] == []

    shared = client.patch(
        f"/v1/dental/doctor/documents/{document_id}",
        json={"patient_visible": True},
    )
    assert shared.status_code == 200, shared.text

    patient_documents = client.get("/v1/dental/patient/documents")
    assert patient_documents.status_code == 200
    visible = patient_documents.json()["result"]
    assert len(visible) == 1
    assert visible[0]["file_name"] == "scan-16.pdf"
    assert "object_key" not in visible[0]
    assert "organisation_id" not in visible[0]
    assert "clinic_id" not in visible[0]
    assert "checksum_sha256" not in visible[0]
    assert "reviewed_at" not in visible[0]
    assert "reviewed_by_doctor_id" not in visible[0]

    patient_download = client.post(
        f"/v1/dental/patient/documents/{document_id}/download-intent"
    )
    assert patient_download.status_code == 200, patient_download.text
    assert patient_download.json()["result"]["expires_in_seconds"] == 120

    doctor_download = client.post(
        f"/v1/dental/doctor/documents/{document_id}/download-intent"
    )
    assert doctor_download.status_code == 200, doctor_download.text
    assert fake_s3.presign_calls[-1]["operation"] == "get_object"
    assert fake_s3.presign_calls[-1]["params"]["Key"] == verified["object_key"]
    assert fake_s3.presign_calls[-1]["expires"] == 120


def test_only_available_documents_can_be_reviewed(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, _fake_s3 = document_api
    intent, _content, _checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]

    response = client.post(f"/v1/dental/doctor/documents/{document_id}/review")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_NOT_REVIEWABLE"
    stored = store.get_document(document_id, "clinic-a")
    assert stored["reviewed_at"] is None
    assert stored["reviewed_by_doctor_id"] is None


def test_patient_identity_cannot_call_doctor_document_review_route(document_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b, _fake_s3 = document_api

    def reject_doctor():
        raise HTTPException(status_code=401, detail="Doctor authentication required")

    client.app.dependency_overrides[dental_documents.require_doctor] = reject_doctor
    response = client.post(
        f"/v1/dental/doctor/documents/{patient_a['id']}/review"
    )
    assert response.status_code == 401


def test_patient_cannot_access_unshared_or_another_patients_document(document_api):
    client, store, _actor, patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/complete").status_code == 200

    # A legacy or bypassed visibility flag must never expose an unreviewed document.
    store._execute("UPDATE dental_documents SET patient_visible=1 WHERE id=?", (document_id,))
    assert store.documents("clinic-a", patient_a["id"], patient_visible_only=True) == []
    assert store.get_document(
        document_id, "clinic-a", patient_id=patient_a["id"], patient_visible_only=True
    ) is None
    assert client.get("/v1/dental/patient/documents").json()["result"] == []
    hidden_download = client.post(f"/v1/dental/patient/documents/{document_id}/download-intent")
    assert hidden_download.status_code == 404

    patient_actor["user_id"] = "supabase-user-b"
    assert client.get("/v1/dental/patient/documents").json()["result"] == []
    other_patient_download = client.post(
        f"/v1/dental/patient/documents/{document_id}/download-intent"
    )
    assert other_patient_download.status_code == 404


def test_doctor_document_routes_are_workspace_scoped(document_api):
    client, store, actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/complete").status_code == 200

    actor["clinic_id"] = "clinic-b"
    assert client.get(
        f"/v1/dental/doctor/patients/{patient_a['id']}/documents"
    ).status_code == 404
    assert client.patch(
        f"/v1/dental/doctor/documents/{document_id}",
        json={"patient_visible": True},
    ).status_code == 404
    assert client.post(
        f"/v1/dental/doctor/documents/{document_id}/review"
    ).status_code == 404


def test_mismatched_upload_is_rejected_and_never_patient_visible(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, _checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content) + 1,
        "ContentType": "application/pdf",
        "Metadata": {"sha256": "0" * 64},
        "Body": content,
    }
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_UPLOAD_VERIFICATION_FAILED"
    assert store.get_document(document_id, "clinic-a")["status"] == "rejected"
    assert stored["upload_object_key"] not in fake_s3.objects
    assert client.get("/v1/dental/patient/documents").json()["result"] == []


def test_invalid_path_filename_is_rejected(document_api):
    client, _store, _actor, _patient_actor, patient_a, _patient_b, _fake_s3 = document_api
    response = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/documents/upload-intent",
        json={
            "file_name": "../private.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "checksum_sha256": "a" * 64,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DOCUMENT_FILE_NAME_INVALID"


def test_document_storage_missing_configuration_returns_503_without_record(document_api, monkeypatch):
    client, store, _actor, _patient_actor, patient_a, _patient_b, _fake_s3 = document_api
    def unavailable():
        raise HTTPException(503, detail={"code": "DOCUMENT_STORAGE_NOT_CONFIGURED"})
    monkeypatch.setattr(dental_documents, "_storage_config", unavailable)
    response = client.post(
        f"/v1/dental/doctor/patients/{patient_a['id']}/documents/upload-intent",
        json={
            "file_name": "scan.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "checksum_sha256": "a" * 64,
        },
    )
    assert response.status_code == 503
    assert store.documents("clinic-a", patient_a["id"]) == []


def test_document_routes_require_doctor_or_patient_authentication():
    app = FastAPI()
    app.include_router(dental_documents.router)
    client = TestClient(app)
    assert client.get("/v1/dental/patient/documents").status_code == 401
    assert client.get("/v1/dental/doctor/patients/missing/documents").status_code == 401


def test_document_checksum_is_verified_against_actual_object_bytes(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": b"X" * len(content),
    }
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_UPLOAD_VERIFICATION_FAILED"
    assert store.get_document(document_id, "clinic-a")["status"] == "rejected"
    assert stored["upload_object_key"] not in fake_s3.objects


def test_replayed_upload_url_cannot_mutate_verified_patient_document(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, "clinic-a")
    upload_key = pending["upload_object_key"]
    fake_s3.objects[upload_key] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }

    completed = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert completed.status_code == 200, completed.text
    assert upload_key not in fake_s3.objects
    verified = store.get_document(document_id, "clinic-a")
    final_key = verified["object_key"]
    assert final_key and final_key != upload_key
    assert fake_s3.objects[final_key]["Body"] == content
    reviewed = client.post(f"/v1/dental/doctor/documents/{document_id}/review")
    assert reviewed.status_code == 200, reviewed.text

    # A still-live presigned PUT can overwrite only the quarantine key.
    replayed_bytes = b"X" * len(content)
    fake_s3.objects[upload_key] = {
        "ContentLength": len(replayed_bytes),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": replayed_bytes,
    }
    shared = client.patch(
        f"/v1/dental/doctor/documents/{document_id}",
        json={"patient_visible": True},
    )
    assert shared.status_code == 200, shared.text
    download = client.post(f"/v1/dental/patient/documents/{document_id}/download-intent")
    assert download.status_code == 200, download.text
    assert fake_s3.presign_calls[-1]["params"]["Key"] == final_key
    assert fake_s3.objects[final_key]["Body"] == content
    assert fake_s3.objects[upload_key]["Body"] == replayed_bytes


def test_document_upload_finalization_can_only_be_claimed_once(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, "clinic-a")
    fake_s3.objects[pending["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/complete").status_code == 200
    second = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert second.status_code == 409
    assert store.get_document(document_id, "clinic-a")["status"] == "available"


def test_storage_config_requires_explicit_private_bucket_attestation(monkeypatch):
    for key in (
        "DENTAL_DOCUMENTS_S3_ENDPOINT",
        "DENTAL_DOCUMENTS_S3_BUCKET",
        "DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID",
        "DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY",
        "DENTAL_DOCUMENTS_STORAGE_PRIVATE",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(HTTPException) as missing:
        dental_documents._storage_config()
    assert missing.value.status_code == 503

    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_ENDPOINT", "https://storage.example")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_BUCKET", "private-documents")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID", "scoped-access")
    monkeypatch.setenv("DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY", "scoped-secret")
    monkeypatch.setenv("DENTAL_DOCUMENTS_STORAGE_PRIVATE", "false")
    with pytest.raises(HTTPException) as not_private:
        dental_documents._storage_config()
    assert not_private.value.status_code == 503

    monkeypatch.setenv("DENTAL_DOCUMENTS_STORAGE_PRIVATE", "true")
    assert dental_documents._storage_config()["bucket"] == "private-documents"


def test_s3_client_failure_rejects_the_pending_upload(document_api, monkeypatch):
    client, store, _actor, _patient_actor, patient_a, _patient_b, _fake_s3 = document_api

    def unavailable(_config):
        raise RuntimeError("storage credential unavailable")

    monkeypatch.setattr(dental_documents, "_get_s3_client", unavailable)
    response, _content, _checksum = _upload_intent(client, patient_a["id"])
    assert response.status_code == 503
    documents = store.documents("clinic-a", patient_a["id"])
    assert len(documents) == 1
    assert documents[0]["status"] == "rejected"


def test_patient_with_multiple_clinic_links_selects_only_the_requested_record(document_api):
    client, store, actor, patient_actor, patient_a, patient_b, fake_s3 = document_api
    store._execute(
        "UPDATE dental_patients SET user_id=? WHERE id=?",
        ("supabase-user-a", patient_b["id"]),
    )
    actor["clinic_id"] = "clinic-b"
    intent, content, checksum = _upload_intent(client, patient_b["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-b")
    fake_s3.objects[stored["upload_object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/complete").status_code == 200
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/review").status_code == 200
    assert client.patch(
        f"/v1/dental/doctor/documents/{document_id}",
        json={"patient_visible": True},
    ).status_code == 200

    ambiguous = client.get("/v1/dental/patient/documents")
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"]["code"] == "PATIENT_RECORD_SELECTION_REQUIRED"

    clinic_a_docs = client.get(
        "/v1/dental/patient/documents",
        params={"patient_id": patient_a["id"]},
    )
    clinic_b_docs = client.get(
        "/v1/dental/patient/documents",
        params={"patient_id": patient_b["id"]},
    )
    assert clinic_a_docs.status_code == 200
    assert clinic_a_docs.json()["result"] == []
    assert clinic_b_docs.status_code == 200
    assert [item["id"] for item in clinic_b_docs.json()["result"]] == [document_id]

    ambiguous_download = client.post(
        f"/v1/dental/patient/documents/{document_id}/download-intent"
    )
    assert ambiguous_download.status_code == 409
    selected_download = client.post(
        f"/v1/dental/patient/documents/{document_id}/download-intent",
        params={"patient_id": patient_b["id"]},
    )
    assert selected_download.status_code == 200

    patient_actor["user_id"] = "supabase-user-b"
    denied = client.get(
        "/v1/dental/patient/documents",
        params={"patient_id": patient_a["id"]},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PATIENT_IDENTITY_MISMATCH"




def test_declared_pdf_with_non_pdf_bytes_is_rejected_and_quarantined(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    content = b"<html><script>not a PDF</script></html>"
    intent, _content, checksum = _upload_intent(client, patient_a["id"], content=content)
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, "clinic-a")
    upload_key = pending["upload_object_key"]
    fake_s3.objects[upload_key] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }

    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_CONTENT_TYPE_MISMATCH"
    assert store.get_document(document_id, "clinic-a")["status"] == "rejected"
    assert upload_key not in fake_s3.objects
    assert not any(key.startswith("verified/") for key in fake_s3.objects)


def test_supported_document_signatures_match_the_declared_media_type():
    assert dental_documents._file_signature_matches("application/pdf", b"%PDF-1.7")
    assert dental_documents._file_signature_matches("image/jpeg", b"\xff\xd8\xff\xe0")
    assert dental_documents._file_signature_matches("image/png", b"\x89PNG\r\n\x1a\n")
    assert dental_documents._file_signature_matches("image/webp", b"RIFF\x00\x00\x00\x00WEBP")

    assert not dental_documents._file_signature_matches("application/pdf", b"<html>")
    assert not dental_documents._file_signature_matches("image/jpeg", b"\x89PNG\r\n\x1a\n")
    assert not dental_documents._file_signature_matches("image/png", b"\xff\xd8\xff\xe0")
    assert not dental_documents._file_signature_matches("image/webp", b"RIFF\x00\x00\x00\x00PNG ")


def test_document_upload_fails_closed_when_private_scanner_is_not_configured(
    document_api, monkeypatch
):
    client, store, actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, actor["clinic_id"])
    upload_key = pending["upload_object_key"]
    fake_s3.objects[upload_key] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }

    def not_configured(_payload):
        raise DocumentScannerNotConfigured("scanner not configured")

    monkeypatch.setattr(dental_documents, "scan_document_bytes", not_configured)
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DOCUMENT_SCAN_NOT_CONFIGURED"
    assert store.get_document(document_id, actor["clinic_id"])["status"] == "pending_upload"
    assert upload_key in fake_s3.objects
    assert not any(key.startswith("verified/") for key in fake_s3.objects)
    retry_actions = [
        row["action"]
        for row in store._execute(
            "SELECT action FROM dental_audit_events WHERE resource_id=?",
            (document_id,),
        )
    ]
    assert "document.upload_verification_retryable" in retry_actions


def test_document_scanner_outage_preserves_the_quarantine_upload_for_retry(
    document_api, monkeypatch
):
    client, store, actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, actor["clinic_id"])
    upload_key = pending["upload_object_key"]
    fake_s3.objects[upload_key] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }

    def unavailable(_payload):
        raise DocumentScannerUnavailable("synthetic scanner outage")

    monkeypatch.setattr(dental_documents, "scan_document_bytes", unavailable)
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DOCUMENT_SCAN_UNAVAILABLE"
    assert store.get_document(document_id, actor["clinic_id"])["status"] == "pending_upload"
    assert upload_key in fake_s3.objects
    assert not any(key.startswith("verified/") for key in fake_s3.objects)


def test_document_with_detected_malware_is_never_published_or_shared(
    document_api, monkeypatch
):
    client, store, actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    pending = store.get_document(document_id, actor["clinic_id"])
    upload_key = pending["upload_object_key"]
    fake_s3.objects[upload_key] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }

    def infected(_payload):
        raise DocumentMalwareDetected("synthetic EICAR detection")

    monkeypatch.setattr(dental_documents, "scan_document_bytes", infected)
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_MALWARE_DETECTED"
    rejected = store.get_document(document_id, actor["clinic_id"])
    assert rejected["status"] == "rejected"
    assert rejected["patient_visible"] in (False, 0)
    assert upload_key not in fake_s3.objects
    assert not any(key.startswith("verified/") for key in fake_s3.objects)
    assert client.get("/v1/dental/patient/documents").json()["result"] == []
