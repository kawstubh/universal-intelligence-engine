# Clinic Assistant permission model

The Clinic Assistant is a separate staff principal, not a Doctor account and not a Doctor membership role.

## Identity and session boundary

- Assistant credentials are stored in \`dental_clinic_assistants\`, not \`dental_doctors\`.
- Assistant clinic assignments are stored in \`dental_clinic_assistant_memberships\`, whose role constraint permits only \`assistant\`.
- Assistant sessions are stored separately in \`dental_assistant_sessions\`; a Doctor session cannot authenticate an Assistant route and an Assistant session receives HTTP 403 from Doctor-only routes.
- Only a clinic \`owner\` or \`admin\` may create or deactivate an Assistant.
- Deactivation revokes active Assistant sessions. Clinic and organisation status are rechecked on each request.

## Patient account-free boundary

- The public patient journey is clinic information and guest appointment requests only.
- Patient accounts, passwords, OTP, signup/login and Supabase patient authentication are disabled.
- Legacy patient-specific API routes return HTTP 403 with `PATIENT_ACCOUNTS_DISABLED` before any Supabase lookup and are omitted from OpenAPI.
- Clinical records, notes, charts, prescriptions, invoices and private documents are not available through public patient routes.
- Patient-specific sharing is deferred until random, scoped, expiring and revocable links have server-side authorization and audit logging.
- Guest booking requires a per-submission idempotency key; only its SHA-256 hash is stored, and retries replay the same opaque receipt.
- A separate normalized request fingerprint suppresses duplicate contact/slot/service requests within a ten-minute window.
- Public responses contain a random receipt reference only; internal patient and appointment IDs are never returned.
- The API enforces future Asia/Kolkata timestamps and the eight published clinic time slots, and rejects public AI requests.
- A server-side honeypot and an eight-request-per-IP/five-minute limiter protect public intake. The limiter is process-local, so a shared edge/WAF limiter remains a production gate for multi-instance scaling.

## Allowed Assistant operations

| Capability | API | Scope |
|---|---|---|
| Patient contact directory | \`GET /v1/dental/assistant/patients\` | Current clinic only; ID, name and phone |
| Contact profile | \`GET /v1/dental/assistant/patients/{patient_id}\` | Current clinic only; no clinical data |
| Patient intake | \`POST /v1/dental/assistant/patients\` | Name and phone only |
| Appointment queue | \`GET /v1/dental/assistant/appointments\` | Current clinic; no clinical note field |
| Appointment request | \`POST /v1/dental/assistant/appointments\` | Existing current-clinic patient; future timezone-aware start; no clinical note |
| Confirm/cancel appointment | \`POST /v1/dental/assistant/appointments/{id}/confirm\`, \`.../cancel\` | Reception status transitions only |
| Billing lookup | \`GET /v1/dental/assistant/patients/{patient_id}/billing\` | Read-only invoice totals and receipt summaries; no item descriptions or patient notes |

## Explicitly denied

Assistant sessions receive HTTP 403 for Doctor-only operations, including:

- Clinical notes, history, treatment plans, chart/odontogram and periodontal records
- Clinical AI, scan analysis, diagnosis support and prescription signing
- Private documents, upload, review, sharing and download
- Invoice creation, issue, void and receipt recording (billing writes remain Doctor-only until the clinic owner approves a precise billing-write scope)
- Organisation/workspace creation, membership management, Doctor account management and owner settings
- Cross-clinic patient, appointment, billing or document access

## Audit

Assistant login/logout, account creation/deactivation, patient directory/profile reads, intake, appointment queue/creation/status changes and billing summary reads are written to \`dental_audit_events\`. Audit records contain actor, action, resource and clinic IDs only; they do not copy names, phone numbers, notes or AI prompts.

## Release constraints

- The role is not complete until CI passes and the separate Assistant browser UI is implemented and verified against these endpoints.
- No Assistant test uses production credentials or production patient data.
- This feature branch and its pull request must remain Draft and must not be merged or deployed without explicit release approval.
