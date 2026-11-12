# Private dental document storage

## Architecture

Document bytes are uploaded directly from the authorised client to a **private S3-compatible object-storage bucket** using a short-lived presigned PUT URL. The FastAPI service stores metadata in PostgreSQL and never writes files to the Render filesystem. It does not proxy the upload through an application-server temporary directory.

The backend verifies the uploaded object before marking it available:
1. The doctor creates an upload intent for a patient in the active workspace.
2. The server generates a tenant-scoped object key and presigned PUT URL.
3. The client uploads with the exact signed `Content-Type` and `x-amz-meta-sha256` headers.
4. The doctor completes the upload.
5. The server checks object size, content type and declared metadata, then streams the object in 64 KiB chunks in memory to verify the actual SHA-256 checksum. No persistent local file is created.
6. Only a verified document can be shared with the patient.
7. Patient download URLs are issued only for documents that are both available and explicitly shared.

## Tenant isolation

Object keys use this structure:

`organisations/{organisation_id}/clinics/{clinic_id}/patients/{patient_id}/{document_id}/{safe_file_name}`

The application derives organisation and clinic IDs from the authenticated doctor workspace. Patient APIs derive the patient record from the authenticated Supabase user; the client cannot choose a patient ID. Patient list/download operations require `status=available` and `patient_visible=true`. Object keys, checksums, organisation IDs and clinic IDs are not returned to patients.

## Limits and supported files

- Maximum file size: 10 MiB.
- Accepted media types: PDF, JPEG, PNG and WebP.
- Upload URL lifetime: default 300 seconds, allowed 60–600.
- Download URL lifetime: default 120 seconds, allowed 30–300.
- Documents are not deleted by this API. No public bucket or permanent public URL is used.
- No patient AI or document interpretation is performed.

## Required staging configuration

Configure these values only in the **staging** backend environment after a dedicated private bucket is created and verified:

- `DENTAL_DOCUMENTS_S3_ENDPOINT`: HTTPS S3-compatible endpoint for the staging object store.
- `DENTAL_DOCUMENTS_S3_REGION`: storage region (Neon Storage currently reports `ap-southeast-1` for the existing project).
- `DENTAL_DOCUMENTS_S3_BUCKET`: dedicated private bucket name.
- `DENTAL_DOCUMENTS_S3_ACCESS_KEY_ID`: scoped staging storage credential.
- `DENTAL_DOCUMENTS_S3_SECRET_ACCESS_KEY`: matching secret; never put it in source control, logs or chat.
- `DENTAL_DOCUMENTS_STORAGE_PRIVATE=true`: explicit operator attestation that the bucket's public access is disabled.
- Optional: `DENTAL_DOCUMENTS_UPLOAD_TTL_SECONDS`, `DENTAL_DOCUMENTS_DOWNLOAD_TTL_SECONDS`.

Use a credential scoped to object read/write for the dedicated bucket/prefix. Do not grant bucket administration, public ACL changes or object deletion to the application credential. Verify private access independently before setting the attestation flag.

When the configuration is absent or the private-bucket attestation is false, upload and download-intent endpoints return `503 DOCUMENT_STORAGE_NOT_CONFIGURED`. The application never falls back to Render's ephemeral filesystem.

## Current infrastructure gate

The Neon project `dr-pranali-dental-recovery` has object storage enabled, but the connected project currently exposes only its `main` branch and no buckets. Public bucket access is disabled at the project storage setting. This PR does **not** create a bucket, create a credential, alter Neon main, or change Render secrets. A dedicated staging branch/bucket and scoped staging credential are required before end-to-end storage can be enabled.

## API

Doctor (existing doctor authentication and active workspace):
- `POST /v1/dental/doctor/patients/{patient_id}/documents/upload-intent`
- `POST /v1/dental/doctor/documents/{document_id}/complete`
- `GET /v1/dental/doctor/patients/{patient_id}/documents`
- `PATCH /v1/dental/doctor/documents/{document_id}` (patient visibility)
- `POST /v1/dental/doctor/documents/{document_id}/download-intent`

Patient (existing Supabase patient authentication; no client-supplied patient ID):
- `GET /v1/dental/patient/documents`
- `POST /v1/dental/patient/documents/{document_id}/download-intent`

## Migration and rollback

Alembic revision `0009_private_dental_documents` follows `0008_patient_billing_plans`. It adds only tenant-scoped metadata; the object store remains private and external. The downgrade is intentionally a no-op to avoid deleting clinical document history during application rollback.
