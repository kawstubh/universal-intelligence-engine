from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_documents
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

    app = FastAPI()
    app.include_router(dental_documents.router)
    app.dependency_overrides[dental_documents.require_doctor] = lambda: actor
    app.dependency_overrides[dental_documents.require_patient] = lambda: patient_actor
    client = TestClient(app)
    return client, store, actor, patient_actor, patient_a, patient_b, fake_s3


def _upload_intent(client: TestClient, patient_id: str, content: bytes = b"synthetic dental document"):
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
    assert stored["object_key"].startswith("organisations/org-a/clinics/clinic-a/patients/")
    assert stored["size_bytes"] == len(content)

    incomplete = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"]["code"] == "DOCUMENT_UPLOAD_INCOMPLETE"

    fake_s3.objects[stored["object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    completed = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["result"]["status"] == "available"

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
    assert fake_s3.presign_calls[-1]["expires"] == 120


def test_patient_cannot_access_unshared_or_another_patients_document(document_api):
    client, store, _actor, patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": content,
    }
    assert client.post(f"/v1/dental/doctor/documents/{document_id}/complete").status_code == 200

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
    fake_s3.objects[stored["object_key"]] = {
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


def test_mismatched_upload_is_rejected_and_never_patient_visible(document_api):
    client, store, _actor, _patient_actor, patient_a, _patient_b, fake_s3 = document_api
    intent, content, _checksum = _upload_intent(client, patient_a["id"])
    document_id = intent.json()["result"]["document"]["id"]
    stored = store.get_document(document_id, "clinic-a")
    fake_s3.objects[stored["object_key"]] = {
        "ContentLength": len(content) + 1,
        "ContentType": "application/pdf",
        "Metadata": {"sha256": "0" * 64},
        "Body": content,
    }
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_UPLOAD_VERIFICATION_FAILED"
    assert store.get_document(document_id, "clinic-a")["status"] == "rejected"
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
    fake_s3.objects[stored["object_key"]] = {
        "ContentLength": len(content),
        "ContentType": "application/pdf",
        "Metadata": {"sha256": checksum},
        "Body": b"X" * len(content),
    }
    response = client.post(f"/v1/dental/doctor/documents/{document_id}/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DOCUMENT_UPLOAD_VERIFICATION_FAILED"
    assert store.get_document(document_id, "clinic-a")["status"] == "rejected"


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
