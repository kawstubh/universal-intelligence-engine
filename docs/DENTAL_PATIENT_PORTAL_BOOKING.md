# Legacy patient account API — disabled for Oralyn

The approved Oralyn product decision is an account-free patient journey:

`Google Search/Maps/Business Profile -> public clinic page -> guest appointment
request -> clinic confirmation`.

Patients do not sign up, log in, create passwords, receive OTPs, or authenticate
through Supabase. The public portal does not show private records, clinical
notes, history, odontograms, prescriptions, invoices or private documents.

## Runtime default

`DENTAL_PATIENT_ACCOUNT_PORTAL_ENABLED=false` is the default and must remain
false in staging and production. With the flag off, legacy patient account,
linking, record-sharing and doctor invite routes return HTTP 404 with
`PATIENT_ACCOUNT_PORTAL_DISABLED`. Patient-facing AI routes remain separately
disabled with `PATIENT_AI_DISABLED`.

Supabase patient-auth settings are not required for the Oralyn public journey
and should remain unset. Do not enable the legacy flag to solve a booking,
navigation or record-display problem.

## Deferred future sharing

If the clinic later approves no-login access to an individual patient's
documents or invoices, design a separate capability-link workflow first:
random high-entropy token, narrow resource scope, short expiry, one-time or
revocable use, server-side authorization, audit events, no PHI in the URL,
no third-party referrers, and no cacheable responses. Do not reactivate the
legacy Supabase patient-account flow as a shortcut.

## Compatibility code

Legacy handlers remain in the repository only for isolated compatibility
tests and controlled future migration work. Tests that exercise those handlers
must explicitly set `DENTAL_PATIENT_ACCOUNT_PORTAL_ENABLED=true` in their
own test process. Never set it in a deployed Oralyn environment.
