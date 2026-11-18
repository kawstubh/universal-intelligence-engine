# Dental public appointment intake

The current public appointment endpoint is a limited guest-intake flow for the
configured master clinic (DENTAL_MASTER_CLINIC_ID, falling back to the legacy
dr-pranali identifier). The patient journey is account-free: no patient login,
password, OTP, signup, Supabase patient session or linked-record API is used.
The endpoint creates a minimal patient contact row and a requested appointment;
it does not create a patient account, accept clinical notes, expose existing
records, return patient/appointment IDs, or invoke AI.

## Request contract

The public form sends:
- A cryptographically random idempotency key (16-128 characters).
- Patient name and contact number (10-digit Indian mobile number (optionally prefixed with +91)).
- A timezone-aware future appointment time.
- One of the 20 service labels in the repository's shared dental service catalogue, or the separate "Other dental concern" fallback.
- intelligence_request=false and an empty honeypot field.

The public service labels are:
- Dental Check-up & Consultation
- Scaling & Cleaning
- Teeth Whitening
- Tooth Filling
- Root Canal Treatment (RCT)
- Dental Crowns & Caps
- Dental Implants
- Tooth Extraction
- Braces Treatment
- Clear Aligners (Invisible Braces)
- Dentures (Full / Partial)
- Veneers (Smile Makeover)
- Gum Treatment (Periodontal Care)
- Kids Dentistry (Child Care)
- Emergency Dental Care
- Tooth Sensitivity Treatment
- Dental Sealants (Cavity Prevention)
- Oral Hygiene Education
- Smile Makeover (Cosmetic Dentistry)
- TMJ & Jaw Pain Treatment
- Other dental concern (fallback)

The API rejects naive/past times, service labels outside the shared 20-service catalogue and fallback, patient-AI requests and
invalid contact details. The current form uses Asia/Kolkata and includes the
+05:30 offset in the submitted timestamp.

## Idempotency and duplicate handling

Migration 0013 adds dental_public_booking_requests. It stores hashes of the
idempotency key, normalised phone number and canonical request payload, plus a
random public reference. It does not store a raw idempotency key or phone hash
input. The idempotency and phone/time unique constraints prevent repeat inserts.

- Same key and same payload returns the original reference with
  status=already_received.
- Same key with changed payload returns HTTP 409.
- Same phone number and exact appointment time with a different key returns
  HTTP 409.
- The public response contains only status, a neutral message and a random
  reference. It never returns patient_id, appointment_id or a record lookup URL.
- A non-empty honeypot receives a neutral response but creates no database row.

The reference is not an access token and has no record lookup endpoint.

## Burst protection

The endpoint enforces an in-process sliding-window limit of eight requests per
client IP per five minutes, returns HTTP 429 with a Retry-After header when
exceeded, and bounds the number of tracked client keys. This is a temporary
single-instance safeguard, not a distributed rate limiter.

Before running multiple API instances or enabling broad public guest booking,
configure an edge/WAF or shared rate-limiting service. Do not rely on the
in-process counter across replicas or restarts.

## Multi-clinic public booking

The current guest form has no public clinic slug or clinic selector and
therefore targets only the configured master clinic. Do not use this endpoint
as a multi-tenant clinic directory. A future multi-clinic public booking flow
must resolve a clinic through a verified, non-secret public slug and apply
per-clinic abuse controls before creating patient rows.


## Required privacy key

Configure `DENTAL_PUBLIC_BOOKING_HASH_KEY` with at least 32 cryptographically random bytes in every environment that accepts public booking. The API fails closed with HTTP 503 if the key is absent or too short. The key is used as an HMAC-SHA-256 secret for the idempotency key, canonical request fingerprint and phone duplicate-detection fingerprint; plain SHA-256 of a 10-digit phone number is not safe because the input space can be enumerated. Keep the value stable across deploys and instances so replay and duplicate detection remain consistent. Treat rotation as a data-compatibility operation and do not silently replace it.
