# Dental treatment plans, invoices and receipts

## Scope

This feature adds persistent, workspace-scoped treatment plans and patient billing records. It does not create payment orders, process card data, or upload documents. Receipts record payments the clinic has already received (cash, UPI, bank transfer, Razorpay reference, or other). Existing SaaS subscription billing remains separate.

## Tenant and patient boundaries

- Every doctor operation is scoped to the authenticated doctor's active `clinic_id` (workspace), supplied by the existing doctor authentication/workspace membership dependency.
- Every write verifies that the patient belongs to that same workspace before inserting data.
- Patient operations derive the patient record from the authenticated Supabase `user_id`; clients cannot supply a patient ID to select another record.
- Patient routes are read-only and return only treatment plans explicitly shared by the clinic and invoices that have been issued and marked patient-visible.
- Draft plans, draft invoices, void invoices, internal workspace identifiers, doctor IDs and receipt idempotency keys are not returned to patients.
- The patient data routes use the patient authentication dependency. They are not AI features and remain available while patient AI actions remain disabled.

## Treatment plans

Table set: `dental_treatment_plans`, `dental_treatment_plan_items`.

- States: `draft -> proposed -> accepted -> in_progress -> completed`; cancellation is permitted from non-terminal states.
- A draft is private. The clinic must move it to `proposed` (or a later active/completed state) and explicitly set `patient_visible=true` before the patient can see it.
- Line items can be replaced only while the plan is a draft.
- Every update increments `version`; clients may send `expected_version` to reject stale edits.
- Completed and cancelled plans are terminal.

## Invoices and receipts

Table set: `dental_invoices`, `dental_invoice_items`, `dental_receipts`.

- All money is integer paise in INR; floating-point currency is never stored.
- Quantity is stored as thousandths (`quantity_milli`), unit price as paise, and tax as basis points (0–2800).
- Invoice-level discount is allocated proportionally to line items **before tax**. The server computes line subtotals, discount allocation, tax and totals; client-supplied totals are not accepted.
- States: `draft -> issued -> partially_paid -> paid`; an unpaid draft/issued/overdue invoice may be voided. Invoices with receipts cannot be voided.
- A draft is private. Issuing the invoice makes it patient-visible.
- Invoice creation requires a workspace-scoped idempotency key; an identical retry returns the existing invoice and reuse with a different payload is rejected.
- Receipts are append-only. The invoice row is locked in PostgreSQL while recording a receipt, the outstanding balance is checked, and an overpayment is rejected.
- Each receipt requires an idempotency key unique per workspace. Repeating the same request returns the original receipt; reusing the key for a different invoice/amount/method is rejected.
- No card number, CVV, bank credential or other payment secret is stored.

## Audit

Creation, update, sharing/issue, receipt and void actions append existing dental audit events. Audit metadata does not contain patient names, amounts, payment references or other clinical/financial payloads.

## API

Doctor (existing doctor email/password auth and active workspace):
- `POST /v1/dental/doctor/patients/{patient_id}/treatment-plans`
- `GET /v1/dental/doctor/patients/{patient_id}/treatment-plans`
- `PATCH /v1/dental/doctor/treatment-plans/{plan_id}`
- `POST /v1/dental/doctor/patients/{patient_id}/invoices`
- `GET /v1/dental/doctor/patients/{patient_id}/invoices`
- `POST /v1/dental/doctor/invoices/{invoice_id}/issue`
- `POST /v1/dental/doctor/invoices/{invoice_id}/receipts`
- `POST /v1/dental/doctor/invoices/{invoice_id}/void`
- `GET /v1/dental/doctor/dashboard/summary` (aggregate counts and INR totals for the active workspace; no patient identifiers)

Patient (existing Supabase patient auth; patient ID is resolved from the authenticated user):
- `GET /v1/dental/patient/treatment-plans`
- `GET /v1/dental/patient/invoices`
- `GET /v1/dental/patient/receipts`

## Migration and rollout

Alembic revision `0008_patient_billing_plans` follows `0007_dental_audit_workspace`. The downgrade is intentionally a no-op so an application rollback does not delete clinical or financial history. Migration rehearsal and PostgreSQL 16 CI are required. This revision must not be applied to Neon main or production without separate explicit approval.

## Documents

Private documents are deliberately not implemented in this change. They require an existing private object-storage bucket, tenant-scoped object keys, upload/download authorization, short-lived signed access and audit logging. The available connected project access does not currently expose a verified private storage bucket or its credentials. Render's ephemeral filesystem is not used.
