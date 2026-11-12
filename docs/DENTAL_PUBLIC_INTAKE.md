# Dental public appointment intake

The current Oralyn patient journey is account-free. The public endpoint
`POST /v1/dental/public/appointments` creates a new patient contact and a
`requested` appointment for the configured master clinic
(`DENTAL_MASTER_CLINIC_ID`, falling back to the legacy `dr-pranali`
identifier). It never creates a patient login, returns a patient identifier,
exposes existing records, or invokes AI.

## Server-side validation and idempotency

- `starts_at` must be a timezone-aware timestamp strictly in the future.
  The portal submits an ISO timestamp normalized from Asia/Kolkata (IST).
- Every request must include a 16-128 character idempotency key.
- The API stores a SHA-256 fingerprint of the canonical request and enforces
  a unique `(clinic_id, idempotency_key)` index.
- A retry with the same key and same payload returns the original appointment
  without creating another patient or appointment. Reusing a key with a
  different payload returns HTTP 409.
- The public response contains only the opaque appointment reference, time,
  service and status. It never returns `patient_id`.
- `intelligence_request` accepts only `false`; public booking cannot invoke
  Doctor-only intelligence.

## Burst protection

The endpoint enforces an in-process sliding-window limit of eight requests per
client IP per five minutes, returns HTTP 429 with a `Retry-After` header when
exceeded, and bounds the number of tracked client keys. Cloudflare's
`CF-Connecting-IP` is used when it contains a valid IP; caller-supplied
`X-Forwarded-For` chains are not trusted.

This is a temporary single-instance safeguard, not a distributed rate limiter.
Before running multiple API instances or enabling broad public guest booking,
configure an edge/WAF or shared rate-limiting service. Do not rely on the
in-process counter across replicas or restarts.

## Single-clinic scope

The current guest form has no public clinic slug or clinic selector and
therefore targets only the configured master clinic. Do not use this endpoint
as a multi-tenant clinic directory. A future multi-clinic public booking flow
must resolve a clinic through a verified, non-secret public slug and apply
per-clinic abuse controls before creating patient rows.
