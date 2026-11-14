"""Doctor-only, tenant-scoped private dental document API.

The API never proxies file bytes through the application server. It issues
short-lived S3-compatible signed URLs for a verified private bucket. Storage
must be explicitly configured; there is no Render filesystem fallback. Patient
document routes and patient-sharing controls are intentionally not mounted.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dental_auth import require_doctor
from .dental_document_scanner import (
    DocumentMalwareDetected,
    DocumentScannerConfigurationError,
    DocumentScannerNotConfigured,
    DocumentScannerUnavailable,
    scan_document_bytes,
)
from .dental_store import get_dental_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dental", tags=["dental-documents"])

MAX_FILE_BYTES = 10 * 1024 * 1024
RETRYABLE_DOCUMENT_SCAN_CODES = {
    "DOCUMENT_SCAN_NOT_CONFIGURED",
    "DOCUMENT_SCAN_CONFIGURATION_INVALID",
    "DOCUMENT_SCAN_UNAVAILABLE",
    "DOCUMENT_SCAN_AUDIT_UNAVAILABLE",
}


class DocumentUploadIntentIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=180)
    content_type: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


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
                "message": "A verified private object-storage bucket and dedicated branch-scoped credentials are required.",
            },
        )
    cleanup_attestation = os.getenv("DENTAL_DOCUMENTS_LIFECYCLE_CLEANUP_VERIFIED", "").strip().lower()
    if cleanup_attestation != "true":
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_STORAGE_LIFECYCLE_NOT_VERIFIED",
                "message": "A tested server-side cleanup process for expired pending uploads is required before document storage can be enabled.",
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


def _file_signature_matches(content_type: str, prefix: bytes) -> bool:
    """Reject a declared media type that does not match the uploaded file header."""
    if content_type == "application/pdf":
        return prefix.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return prefix.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return prefix.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
    return False


def _doctor_document(document: dict) -> dict:
    return {
        "id": document["id"],
        "patient_id": document["patient_id"],
        "file_name": document["file_name"],
        "content_type": document["content_type"],
        "size_bytes": int(document["size_bytes"]),
        "status": document["status"],
        "patient_visible": bool(document["patient_visible"]),
        "reviewed_by_doctor_id": document.get("reviewed_by_doctor_id"),
        "reviewed_at": document.get("reviewed_at"),
        "created_at": document["created_at"],
        "uploaded_at": document["uploaded_at"],
    }


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


def _reject_pending_upload_safely(store, document_id: str, actor: dict) -> None:
    """Best-effort cleanup that never masks the original upload failure."""
    try:
        store.mark_document_rejected(document_id, actor["clinic_id"], actor["id"])
    except Exception:
        logger.exception("Could not reject pending dental document upload %s", document_id)


def _release_document_upload_claim_safely(store, document_id: str, actor: dict) -> None:
    """Keep verified quarantine bytes retryable when the scanner is temporarily unavailable."""
    try:
        released = store.release_document_upload_claim(
            document_id, actor["clinic_id"], actor["id"]
        )
        if not released:
            logger.warning(
                "Could not release document verification claim for retry (document %s)",
                document_id,
            )
    except Exception:
        logger.exception(
            "Could not release document verification claim for retry (document %s)",
            document_id,
        )


def _delete_object_safely(s3, bucket: str, key: str) -> None:
    """Remove quarantine/orphan bytes without masking the primary API result."""
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        key_fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        logger.exception(
            "Could not remove private dental document object (key fingerprint %s)",
            key_fingerprint,
        )


def _scan_document_or_raise(payload: bytes, store, actor: dict, document_id: str) -> None:
    """Require a clean private ClamAV result before publishing document bytes."""
    try:
        scan_document_bytes(payload)
    except DocumentMalwareDetected as exc:
        try:
            store.audit(
                actor["id"],
                "document.malware_detected",
                "document",
                document_id,
                actor["clinic_id"],
            )
        except Exception:
            logger.exception("Could not record a blocked document malware scan")
        raise HTTPException(
            409,
            detail={
                "code": "DOCUMENT_MALWARE_DETECTED",
                "message": "The uploaded document was blocked by the clinic security scanner.",
            },
        ) from exc
    except DocumentScannerNotConfigured as exc:
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_SCAN_NOT_CONFIGURED",
                "message": "Private malware scanning is not configured; document uploads are disabled.",
            },
        ) from exc
    except DocumentScannerConfigurationError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_SCAN_CONFIGURATION_INVALID",
                "message": "The private document scanner configuration is invalid.",
            },
        ) from exc
    except DocumentScannerUnavailable as exc:
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_SCAN_UNAVAILABLE",
                "message": "The clinic security scanner is unavailable; document upload was rejected.",
            },
        ) from exc

    try:
        store.audit(
            actor["id"],
            "document.malware_scan_clean",
            "document",
            document_id,
            actor["clinic_id"],
        )
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "DOCUMENT_SCAN_AUDIT_UNAVAILABLE",
                "message": "The document security scan could not be recorded; upload was rejected.",
            },
        ) from exc


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
        _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
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
        signature_prefix = bytearray()
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
                if len(signature_prefix) < 12:
                    signature_prefix.extend(chunk[: 12 - len(signature_prefix)])
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
        if not _file_signature_matches(document["content_type"], bytes(signature_prefix)):
            raise HTTPException(
                409,
                detail={"code": "DOCUMENT_CONTENT_TYPE_MISMATCH", "message": "Uploaded bytes do not match the declared PDF or image type."},
            )

        # A checksum and file signature do not establish that a file is safe.
        # Only a clean result from the private ClamAV service can be published.
        _scan_document_or_raise(verified_bytes.getvalue(), store, actor, document_id)

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
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") in RETRYABLE_DOCUMENT_SCAN_CODES:
            # Keep the quarantine object and allow retry after scanner/audit recovery.
            _release_document_upload_claim_safely(store, document_id, actor)
            raise
        _reject_pending_upload_safely(store, document_id, actor)
        _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
        _delete_object_safely(s3, config["bucket"], final_object_key)
        raise
    except ValueError as exc:
        _reject_pending_upload_safely(store, document_id, actor)
        _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
        _delete_object_safely(s3, config["bucket"], final_object_key)
        raise HTTPException(409, detail={"code": "DOCUMENT_UPLOAD_NOT_PENDING", "message": "Document upload state changed before finalization."}) from exc
    except Exception as exc:
        _reject_pending_upload_safely(store, document_id, actor)
        _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
        _delete_object_safely(s3, config["bucket"], final_object_key)
        raise HTTPException(503, detail={"code": "DOCUMENT_STORAGE_UNAVAILABLE", "message": "Verified document could not be published to private storage."}) from exc

    if not result:
        _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
        _delete_object_safely(s3, config["bucket"], final_object_key)
        raise HTTPException(404, detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"})
    _delete_object_safely(s3, config["bucket"], document["upload_object_key"])
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


@router.post("/doctor/documents/{document_id}/review")
def review_document(
    document_id: str,
    actor: Annotated[dict, Depends(require_doctor)],
):
    store = get_dental_store()
    try:
        result = store.review_document(document_id, actor["clinic_id"], actor["id"])
    except ValueError as exc:
        raise HTTPException(
            409,
            detail={"code": "DOCUMENT_NOT_REVIEWABLE", "message": str(exc)},
        ) from exc
    if not result:
        raise HTTPException(
            404,
            detail={"code": "DOCUMENT_NOT_FOUND", "message": "Document not found in this workspace"},
        )
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
