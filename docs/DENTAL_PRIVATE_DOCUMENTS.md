# Doctor-only private dental document storage

## Product boundary

The Oralyn patient journey is public and account-free. It has no patient accounts, password, OTP, signup/login, Supabase patient authentication, linked-record access, or patient document downloads.

Clinical documents are private to the authenticated Doctor workspace. Patient-specific no-login sharing is deferred until a separate design uses random, scoped, expiring and revocable links with server authorization and audit logging. A public booking reference is never a document-access token.

## Architecture

Document bytes are uploaded directly from the authorised Doctor client to a **private S3-compatible object-storage bucket** using a short-lived presigned PUT URL. The FastAPI service stores metadata in PostgreSQL and never writes files to the Render filesystem.

The backend verifies the object before marking it available:

1. The Doctor creates an upload intent for a patient in the active workspace.
2. The server generates a tenant-scoped object key and presigned PUT URL.
3. The client uploads with the exact signed `Content-Type` and `x-amz-meta-sha256` headers.
4. The Doctor completes the upload.
5. The server checks size, content type and declared metadata, atomically claims the pending upload, then streams the object in bounded chunks while verifying the actual SHA-256 checksum.
6. The server checks the file signature against the declared PDF/JPEG/PNG/WebP media type.
7. The server scans the verified bytes with private ClamAV using the bounded `INSTREAM` protocol.
8. Only a clean scan may be copied to a separate `verified/` object key. The server confirms final size, content type and checksum metadata before storing that final key.
9. The pending/quarantine object is deleted best-effort. Doctor downloads always use the separate verified key, so replay of the original upload URL cannot mutate a published document.
10. A Doctor may record an internal clinical review with Doctor ID and timestamp. This is not a patient-sharing permission.

No public bucket, permanent public URL, patient document endpoint or patient download-intent route is mounted. No patient AI or document interpretation is performed.

## Tenant isolation

Quarantine upload keys use:

`pending/organisations/{organisation_id}/clinics/{clinic_id}/patients/{patient_id}/{document_id}/{safe_file_name}`

Verified download keys use a separate prefix:

`verified/organisations/{organisation_id}/clinics/{clinic_id}/patients/{patient_id}/{document_id}/{safe_file_name}`

The upload key is never used for downloads. The application derives organisation and clinic IDs from the authenticated Doctor workspace; the client cannot choose another clinic. The database status transition (`pending_upload` → `verifying` → `available`) is claimed atomically so only one completion request can publish a verified object.

## Limits and supported files

- Maximum file size: 10 MiB.
- Accepted media types: PDF, JPEG, PNG and WebP, with server-side signature validation.
- Upload URL lifetime: default 300 seconds, allowed 60–600.
- Doctor download URL lifetime: default 120 seconds, allowed 30–300.
- No local filesystem fallback.
- Malware scanning is mandatory; signature/checksum validation and Doctor review are not substitutes for antivirus.
- Missing/unavailable scanner configuration fails closed with HTTP 503. A malware finding returns HTTP 409. No unscanned object can become available.
- Patient sharing and patient download URLs are disabled.

## Required staging configuration

Configure only on the isolated synthetic staging backend:

- `DENTAL_DOCUMENTS_S3_ENDPOINT`: HTTPS S3-compatible endpoint.
- `DENTAL_DOCUMENTS_S3_REGION`: storage region.
- `DENTAL_DOCUMENTS_S3_BUCKET`: dedicated private bucket.
- `DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID` and `DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY`: dedicated staging credential; never commit, log or paste it into chat.
- `DENTAL_DOCUMENTS_STORAGE_PRIVATE=true`: set only after independently verifying the bucket is private.
- Optional upload/download TTL settings.

### Current isolated staging bucket

- Neon project: `silent-king-54610898`.
- Branch: `br-steep-bonus-a1yqarg7`.
- Private bucket: `oralyn-private-documents`, created 13 November 2026.
- The bucket is empty. No storage credential has been issued or installed in Render. Uploads remain disabled.
- Neon Free includes up to 5 GB of object storage per project; keep this synthetic-only and within the included allowance.

Neon storage credentials are branch-scoped, not bucket/prefix-scoped. The `storage:write` scope includes object reads/writes, object deletion and bucket creation. Keep the staging branch/project isolated with only this dedicated private bucket. Do not reuse its credential in production or across unrelated buckets. If production requires bucket/prefix-scoped credentials, use a provider that supports those scopes or a separate isolated storage project.

**Neon Object Storage lifecycle rules are not currently enforced.** Do not rely on a configured S3 lifecycle rule to expire `pending/` objects. Before enabling uploads, implement and test a server-side cleanup process for expired pending objects, or use a storage provider with enforced lifecycle expiration. This is a release gate because replayed presigned PUT URLs and abandoned uploads must not leave unbounded quarantine objects.

When storage configuration is absent or private-bucket attestation is false, Doctor upload/download-intent endpoints return `503 DOCUMENT_STORAGE_NOT_CONFIGURED`. The application never falls back to Render's ephemeral filesystem.

## Mandatory private ClamAV scanning

Configure only after a dedicated scanner is reachable on the backend's private network:

- `DENTAL_DOCUMENT_SCANNER_HOST`: internal hostname or private IP.
- `DENTAL_DOCUMENT_SCANNER_PORT=3310`.
- `DENTAL_DOCUMENT_SCANNER_TIMEOUT_SECONDS=20` (allowed range 1–60).
- `DENTAL_DOCUMENT_SCANNER_PRIVATE=true`: explicit private-network attestation.

The backend resolves the scanner host and rejects public, multicast, unspecified and link-local addresses before connecting to the resolved private address. It sends no file bytes to a public scanning vendor. Set ClamAV `StreamMaxLength` to at least 10 MiB, keep virus definitions current, and monitor scanner freshness and availability.

The CI suite includes an isolated official ClamAV 1.5.4 service and tests a clean payload plus the EICAR test signature. This verifies the application protocol against a real scanner in CI; it does **not** provision or attest a staging or production scanner.

No ClamAV service is currently provisioned in Render staging. The upload pipeline remains fail-closed until a private scanner is independently deployed and verified.

## Doctor API

- `POST /v1/dental/doctor/patients/{patient_id}/documents/upload-intent`
- `POST /v1/dental/doctor/documents/{document_id}/complete`
- `GET /v1/dental/doctor/patients/{patient_id}/documents`
- `POST /v1/dental/doctor/documents/{document_id}/review` (internal clinical review)
- `POST /v1/dental/doctor/documents/{document_id}/download-intent`

There are no `/v1/dental/patient/documents` routes. The Doctor visibility/share route is not mounted. Patient sharing requires a future scoped, expiring and revocable-link design and separate release approval.

## Migration and rollback

Alembic revision `0009_private_dental_documents` follows `0008_patient_billing_plans`. Revision `0011_document_clinical_review` follows `0010_clinical_notes_messaging` and adds nullable review-provenance columns. Existing metadata is preserved. Patient visibility fields remain inert for compatibility and are not exposed through a patient API. Downgrades are intentionally no-ops to avoid deleting clinical document history during application rollback.
