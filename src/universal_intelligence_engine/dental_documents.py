"""Private, tenant-scoped dental document API.

The API never proxies file bytes through the application server. It issues
short-lived S3-compatible signed URLs for a verified private bucket. Storage
must be explicitly configured; there is no Render filesystem fallback.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dental_ai import require_patient
from .dental_auth import require_doctor
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-documents"])

MAX_FILE_BYTES = 10 * 1024 * 1024
class DocumentUploadIntentIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=180)
    content_type: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class DocumentVisibilityIn(BaseModel):
    patient_visible: bool


def _envelope(result):
    return {
        "result": result,
        "confidence": 1.0,
        "sources": [],
        "request_id": str(uuid.uuid4()),
    }


def _storage_config() -> dict:
    endpoint = os.getenv("DENTAL_DOCUMENTS_S3_ENDPOINT", "").strip()
    bucket = os.getenv("DENTAL_DOCUMENTS_S3_BUCKET", "").strip()
    access_key = os.getenv("DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY", "")
    region = os.getenv("DENTAL_DOCUMENTS_S3_REGION", "ap-southeast-1").strip()
    private_attestation = os.getenv("DENTAL_DOCUMENTS_STORAGE_PRIVATE", "").strip().lower()
    parsed = urlparse(endpoint)
    if (
        not endpoint
        or parsed.scheme != "https"
        or not parsed.netloc
        or not bucket
        or not access_key
        or not secret_key
        or private_attestation != "true"
    ):
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_STORAGE_NOT_CONFIGURED",
                "message": "A verified private object-storage bucket and scoped credentials are required.",
            },
        )
    upload_ttl = int(os.getenv("DENTAL_DOCUMENTS_UPLOAD_TTL_SECONDS", "300"))
    download_ttl = int(os.getenv("DENTAL_DOCUMENTS_DOWNLOAD_TTL_SECONDS", "120"))
    if not 60 <= upload_ttl <= 600 or not 30 <= download_ttl <= 300:
        raise HTTPException(
            503,
            detail={"code": "DOCUMENT_STORAGE_CONFIG_INVALID", "message": "Document URL lifetime is outside the allowed range."},
        )
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "region": region,
        "upload_ttl": upload_ttl,
        "download_ttl": download_ttl,
    }


def _get_s3_client(config: dict):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise HTTPException(
            503,
            detail={"code": "DOCUMENT_STORAGE_CLIENT_MISSING", "message": "Private object-storage client is unavailable."},
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint"],
        region_name=config["region"],
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret_key"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _safe_file_name(file_name: str) -> str:
    if "/" in file_name or "\\" in file_name or file_name in {".", ".."}:
        raise HTTPException(400, detail={"code": "DOCUMENT_FILE_NAME_INVALID", "message": "File name must not contain a path."})
    name = PurePosixPath(file_name).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise HTTPException(400, detail={"code": "DOCUMENT_FILE_NAME_INVALID", "message": "File name is invalid."})
    return name[:180]


def _doctor_document(document: dict) -> dict:
    return {
        "id": document["id"],
        "patient_id": document["patient_id"],
        "file_name": document["file_name"],
        "content_type": document["content_type"],
        "size_bytes": int(document["size_bytes"]),
        "status": document["status"],
        "patient_visible": bool(document["patient_visible"]),
        "created_at": document["created_at"],
        "uploaded_at": document["uploaded_at"],
    }


def _patient_document(document: dict) -> dict:
    return {
        "id": document["id"],
        "file_name": document["file_name"],
        "content_type": document["content_type"],
        "size_bytes": int(document["size_bytes"]),
        "created_at": document["created_at"],
        "uploaded_at": document["uploaded_at"],
    }


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
    if linked:
        return store.patient_by_user_id(actor["user_id"], linked[0]["patient_id"])
    raise HTTPException(
        404,
        detail={
            "code": "PATIENT_RECORD_NOT_LINKED",
            "message": "No clinic record is linked to this patient account.",
        },
    )


def _presigned_download(document: dict, config: dict, s3) -> dict:
    if document["status"] != "available" or not document.get("object_key"):
        raise HTTPException(409, detail={"code": "DOCUMENT_NOT_AVAILABLE", "message": "Document is not available for download."})
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": config["bucket"],
                "Key": document["object_key"],
                "ResponseContentType": document["content_type"],
                "ResponseContentDisposition": f'attachment; filename="{document["file_name"]}"',
            },
            ExpiresIn=config["download_ttl"],
        )
    except Exception as exc:
        raise HTTPException(503, detail={"code": "DOCUMENT_STORAGE_UNAVAILABLE", "message": "Document storage is temporarily unavailable."}) from exc
    return {"download_url": url, "expires_in_seconds": config["download_ttl"]}


@router.post("/doctor/patients/{patient_id}/documents/upload-intent")
def create_document_upload_intent(
    patient_id: str,
    body: DocumentUploadIntentIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    config = _storage_config()
    store = get_dental_store()
    patient = store.get_patient(patient_id, actor["clinic_id"])
    if not patient:
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    clinic = store.get_clinic(actor["clinic_id"])
    if not clinic:
        raise HTTPException(404, detail={"code": "CLINIC_NOT_FOUND", "message": "Active clinic workspace was not found"})
    safe_name = _safe_file_name(body.file_name)
    document_id = uuid.uuid4().hex
    upload_object_key = (
        f"pending/organisations/{clinic['organisation_id']}/clinics/{actor['clinic_id']}/"
        f"patients/{patient_id}/{document_id}/{safe_name}"
    )
    data = {
        "id": document_id,
        "organisation_id": clinic["organisation_id"],
        "patient_id": patient_id,
        "file_name": safe_name,
        "content_type": body.content_type,
        "size_bytes": body.size_bytes,
        "checksum_sha256": body.checksum_sha256.lower(),
        "upload_object_key": upload_object_key,
    }
    try:
        document = store.create_document(data, actor["clinic_id"], actor["id"])
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "DOCUMENT_PATIENT_NOT_FOUND", "message": str(exc)}) from exc
    try:
        s3 = _get_s3_client(config)
        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": config["bucket"],
                "Key": upload_object_key,
                "ContentType": body.content_type,
                "Metadata": {"sha256": body.checksum_sha256.lower()},
            },
            ExpiresIn=config["upload_ttl"],
            HttpMethod="PUT",
        )
    except Exception as exc:
        store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, detail={"code": "DOCUMENT_STORAGE_UNAVAILABLE", "message": "Document storage is temporarily unavailable."}) from exc
    return _envelope({
        "document": _doctor_document(document),
        "upload_url": url,
        "required_headers": {
            "Content-Type": body.content_type,
            "x-amz-meta-sha256": body.checksum_sha256.lower(),
        },
        "expires_in_seconds": config["upload_ttl"],
    })


@router.post("/doctor/documents/{document_id}/complete")
def complete_document_upload(
    document_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    config = _storage_config()
    store = get_dental_store()
    document = store.get_document(document_id, actor["clinic_id"])
    if not document:
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"})
    if document["status"] != "pending_upload":
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_NOT_PENDING", "message": "Document upload is not pending."})

    s3 = _get_s3_client(config)
    try:
        head = s3.head_object(Bucket=config["bucket"], Key=document["upload_object_key"])
    except Exception as exc:
        # A client may still be uploading. Keep the intent pending so it can retry.
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_INCOMPLETE", "message": "Uploaded document could not be verified."}) from exc

    metadata = {str(key).lower(): str(value).lower() for key, value in (head.get("Metadata") or {}).items()}
    header_valid = (
        int(head.get("ContentLength", -1)) == int(document["size_bytes"])
        and head.get("ContentType") == document["content_type"]
        and metadata.get("sha256") == document["checksum_sha256"].lower()
    )
    if not header_valid:
        store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_VERIFICATION_FAILED", "message": "Uploaded document did not match its declared size, type or checksum."})

    # Presigned PUT URLs are reusable until expiry. Claim the database row once,
    # then publish only bytes read and verified by this request under a different key.
    if not store.claim_document_upload(document_id, actor["clinic_id"], actor["id"]):
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_NOT_PENDING", "message": "Document upload is already being verified or has been completed."})

    final_object_key = (
        f"verified/organisations/{document['organisation_id']}/clinics/{actor['clinic_id']}/"
        f"patients/{document['patient_id']}/{document_id}/{document['file_name']}"
    )
    try:
        actual_size = 0
        actual_digest = hashlib.sha256()
        verified_bytes = io.BytesIO()
        stored_object = s3.get_object(
            Bucket=config["bucket"], Key=document["upload_object_key"]
        )
        body = stored_object["Body"]
        try:
            while True:
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                actual_size += len(chunk)
                if actual_size > MAX_FILE_BYTES:
                    raise HTTPException(
                        409,
                        detail={"code": "DOCUMENT_UPLOAD_VERIFICATION_FAILED", "message": "Uploaded document exceeds the allowed size."},
                    )
                actual_digest.update(chunk)
                verified_bytes.write(chunk)
        finally:
            body.close()

        valid = (
            actual_size == int(document["size_bytes"])
            and actual_digest.hexdigest() == document["checksum_sha256"].lower()
        )
        if not valid:
            raise HTTPException(
                409,
                detail={"code": "DOCUMENT_UPLOAD_VERIFICATION_FAILED", "message": "Uploaded document bytes did not match the declared SHA-256 checksum."},
            )

        verified_bytes.seek(0)
        s3.put_object(
            Bucket=config["bucket"],
            Key=final_object_key,
            Body=verified_bytes,
            ContentType=document["content_type"],
            Metadata={"sha256": document["checksum_sha256"].lower()},
        )
        final_head = s3.head_object(Bucket=config["bucket"], Key=final_object_key)
        final_metadata = {
            str(key).lower(): str(value).lower()
            for key, value in (final_head.get("Metadata") or {}).items()
        }
        if (
            int(final_head.get("ContentLength", -1)) != actual_size
            or final_head.get("ContentType") != document["content_type"]
            or final_metadata.get("sha256") != document["checksum_sha256"].lower()
        ):
            raise HTTPException(
                409,
                detail={"code": "DOCUMENT_UPLOAD_VERIFICATION_FAILED", "message": "Verified object could not be confirmed in private storage."},
            )

        result = store.mark_document_uploaded(
            document_id, actor["clinic_id"], actor["id"], final_object_key
        )
    except HTTPException:
        try:
            store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
        except Exception:
            pass
        raise
    except ValueError as exc:
        try:
            store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
        except Exception:
            pass
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_NOT_PENDING", "message": "Document upload state changed before finalization."}) from exc
    except Exception as exc:
        try:
            store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
        except Exception:
            pass
        raise HTTPException(503, detail={"code": "DOCUMENT_STORAGE_UNAVAILABLE", "message": "Verified document could not be published to private storage."}) from exc

    if not result:
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"})
    return _envelope(_doctor_document(result))


@router.get("/doctor/patients/{patient_id}/documents")
def list_doctor_documents(
    patient_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    if not store.get_patient(patient_id, actor["clinic_id"]):
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this workspace"})
    return _envelope([
        _doctor_document(item)
        for item in store.documents(actor["clinic_id"], patient_id, patient_visible_only=False)
    ])


@router.patch("/doctor/documents/{document_id}")
def set_document_visibility(
    document_id: str,
    body: DocumentVisibilityIn,
    actor: Annotated[dict, Depends(require_doctor)],
):
    try:
        result = get_dental_store().set_document_visibility(
            document_id, actor["clinic_id"], actor["id"], body.patient_visible
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "DOCUMENT_NOT_SHAREABLE", "message": str(exc)}) from exc
    if not result:
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"})
    return _envelope(_doctor_document(result))


@router.post("/doctor/documents/{document_id}/download-intent")
def create_doctor_document_download_intent(
    document_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    config = _storage_config()
    store = get_dental_store()
    document = store.get_document(document_id, actor["clinic_id"])
    if not document:
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"})
    s3 = _get_s3_client(config)
    result = _presigned_download(document, config, s3)
    store.audit(actor["id"], "document.download_intent_created", "document", document_id, actor["clinic_id"])
    return _envelope(result)


@router.get("/patient/documents")
def list_patient_documents(
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    patient = _patient_for_actor(actor, patient_id)
    documents = get_dental_store().documents(
        patient["clinic_id"], patient["id"], patient_visible_only=True
    )
    return _envelope([_patient_document(item) for item in documents])


@router.post("/patient/documents/{document_id}/download-intent")
def create_patient_document_download_intent(
    document_id: str,
    actor: Annotated[dict, Depends(require_patient)],
    patient_id: str | None = None,
):
    config = _storage_config()
    patient = _patient_for_actor(actor, patient_id)
    document = get_dental_store().get_document(
        document_id,
        patient["clinic_id"],
        patient_id=patient["id"],
        patient_visible_only=True,
    )
    if not document:
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found"})
    result = _presigned_download(document, config, _get_s3_client(config))
    get_dental_store().audit(
        actor["user_id"], "document.patient_download_intent_created",
        "document", document_id, patient["clinic_id"],
    )
    return _envelope(result)
