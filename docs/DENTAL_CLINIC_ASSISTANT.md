# Clinic Assistant access model

Clinic Assistants are separate staff identities. They are not Doctor accounts,
Doctor memberships, or a reduced Doctor UI session. Assistant credentials and
sessions live in their own tables and use a dedicated bearer dependency.

## Allowed API matrix

| Capability | Assistant API | Scope |
|---|---|---|
| Patient intake | GET/POST /v1/dental/assistant/patients | Name, validated Indian mobile number and created time only; current clinic |
| Appointment queue | GET /v1/dental/assistant/appointments | Patient contact, requested time, service label and status; current clinic |
| Confirm/reschedule/cancel | PATCH /v1/dental/assistant/appointments/{id} | Status and timezone-aware future start only; reschedule requires a new time; no notes |
| Billing desk | GET /v1/dental/assistant/billing/invoices | Issued invoice balance summary; no line items or clinical notes |
| Record payment | POST /v1/dental/assistant/billing/invoices/{id}/receipts | Cash, UPI, bank transfer or other payment already received; idempotent |
| Assistant account administration | /v1/dental/clinic-assistants | Owner/admin only; one active clinic |

## Explicitly denied

Assistants cannot call Doctor patient directory/profile, chart/odontogram,
periodontogram, clinical notes, treatment plans, document upload/download/share,
clinical intelligence, scan screening, Doctor workspace switching, organisation
or owner settings, membership administration, invoice creation/issue/void, or
patient account/linking endpoints. These remain behind require_doctor or
require_patient; a valid Assistant bearer token receives HTTP 403 on Doctor
routes. A Doctor token is not accepted by Assistant routes.

## Security properties

- Assistant login uses its own account table and eight-hour session table.
- Passwords use the existing scrypt password hashing implementation.
- Only token hashes are stored; logout and account deactivation revoke sessions.
- Every assistant data query includes the authenticated clinic ID.
- Account administration is owner/admin-only and binds assistants to the
  administrator's current clinic.
- Intake and appointment actions, billing receipts, account changes and
  revocations are audit logged without storing patient names, phone numbers,
  clinical text or payment details in audit metadata.
- Billing collection records money already received. It does not initiate
  payment gateway transactions, create invoices, edit line items, issue or
  void invoices, or sign prescriptions.


## Sign-in abuse protection

Assistant sign-in uses a short in-process sliding window keyed by a validated Cloudflare `CF-Connecting-IP` address on Render, with a bounded client map to prevent unbounded memory growth. This protects the current single-instance synthetic staging service. Before production runs multiple API instances, add a shared/distributed rate limiter or a trusted edge/WAF policy; do not treat the in-process limiter as cluster-wide protection.
