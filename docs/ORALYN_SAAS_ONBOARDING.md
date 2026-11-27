# Oralyn SaaS onboarding and entitlement specification

Status: approved product requirements; implementation work tracked on this branch.
Branch: `feat/oralyn-trial-provisioning-v1`.

## Commercial rules

- Dr. Pranali Dental Clinic is a permanent lifetime-free founding clinic. This is a clinic entitlement, not a coupon or expiring trial. It must be enforced by the backend and must never enter checkout, trial expiry, or subscription dunning.
- Every other newly registered clinic receives one 3-day demo beginning when its clinic workspace is successfully provisioned.
- Demo expiry is calculated and stored server-side. Browser clocks and frontend state are never authoritative.
- At expiry, full paid features are blocked until a paid entitlement is confirmed. Do not silently charge a card or start a paid subscription; the clinic owner must explicitly choose a plan and consent to checkout.
- Successful verified payment activates the selected plan. Webhook processing must be signature-verified, replay-safe, idempotent, and linked to the exact clinic/order.
- Subscription states must cover trialing, active, past_due, cancelled, expired, paused, and lifetime_free (or an equivalent immutable entitlement model).
- Grace-period policy must be configurable and disclosed before launch; do not invent a grace period in code.

## Tenant provisioning

Each new clinic gets a unique organisation/workspace and a clinic owner account. Provisioning must be atomic or safely retryable: no orphaned clinic, duplicate owner, or duplicate trial on repeated form submission.

All clinic-owned data, patient records, appointments, CRM records, staff memberships, audit events, billing records and files must be scoped to the clinic. Backend authorization is authoritative; UI-only filtering is not sufficient.

## Patient and appointment experience — no patient accounts

- Patients do not create accounts and do not use passwords, OTP, signup/login, Supabase patient authentication, patient portals or patient-specific record access.
- The public clinic journey is limited to clinic information, treatment selection, appointment requests and callback requests.
- A booking request is not a confirmed appointment until clinic staff accepts or reschedules it.
- The request form collects only the minimum necessary details: patient name, mobile number, requested service, preferred date/time and optional short note.
- The clinic Doctor/Reception workspace manages incoming requests and their status (new, contacted, accepted/confirmed, rescheduled, declined, closed).
- A missed telephone call cannot be detected by the website alone. Missed-call capture requires a connected telephony provider/webhook or manual receptionist entry. Until telephony is configured, provide a callback-request path and a reception work queue.
- No public route may expose clinical notes, dental charts, prescriptions, treatment plans, invoices, receipts, patient history or private documents.
- AI clinical intelligence remains Doctor-only. No patient-facing diagnosis, triage or self-diagnosis.

## Website demo request

The public Oralyn website collects only necessary clinic-owner business contact and clinic details. A successful request:

1. validates and rate-limits the request;
2. creates or safely reuses a pending onboarding request;
3. verifies the clinic owner's email/phone before public self-service provisioning is enabled;
4. provisions one clinic workspace and owner identity atomically or through a safely retryable operation;
5. records `trial_started_at` and `trial_ends_at = trial_started_at + 72 hours` when provisioning succeeds;
6. sends a secure account-activation link (never emails a plaintext password);
7. shows the exact demo expiry time and a clear, explicit subscription path.

Repeated submissions must not create multiple workspaces or restart an existing trial. Public self-service provisioning must remain disabled until verification, abuse controls, tenant isolation and rollback are tested.

## Master administration

The Oralyn master admin console must show clinics, owner/contact, onboarding state, trial start/end, entitlement, plan, payment status, renewal, cancellation and audit history. Master admin operational access must not imply unrestricted access to clinical records. Exceptional support access, if ever added, must be time-limited, purpose-bound and audited.

Dr. Pranali's founding entitlement must be assigned by protected server-side configuration/seed or explicit admin operation, not by a user-editable clinic name or request field.

## Existing implementation discovered during preparation

- Backend already has clinic/workspace membership and doctor password authentication.
- Backend already exposes plan listing, membership lookup, Razorpay order creation, and a Razorpay webhook.
- Existing billing has a master-clinic ID free-account check but does not yet implement a complete 72-hour trial lifecycle or public website self-service provisioning.
- Existing plan prices are ₹4,999/month (Clinic) and ₹9,999/month (Enterprise); these are existing code values, not a newly approved pricing decision.
- Current patient journey is public and account-free appointment requests. This is the required patient scope; patient accounts/login are explicitly out of scope.
- The current desktop folder is a React/Vite browser/PWA portal, not a packaged Windows desktop application.
- Current deployed guest booking is configured for the Dr. Pranali master clinic. A multi-clinic public directory/tenant slug and verified clinic-specific public booking routing are still required before public booking can be opened to all clinics.

## Required implementation sequence

1. Add migration-backed trial and entitlement state, including an immutable lifetime-free founding-clinic entitlement.
2. Implement secure, idempotent clinic provisioning and owner activation; add server-side trial status/expiry enforcement.
3. Harden Razorpay order/webhook mapping so paid entitlement is applied only to the exact clinic/order.
4. Add public demo-request and owner activation flow to the Oralyn marketing website.
5. Build Master Admin clinic, trial, subscription and audit management.
6. Complete public account-free booking, clinic-side request handling, callback queue and optional telephony missed-call integration.
7. Complete clinic CRM, staff roles and clinic branding/configuration.
8. Add tests for duplicate submissions, trial expiry boundaries, founding-clinic immunity, payment webhook replay, cross-tenant access, public booking isolation, request status transitions and account suspension.
9. Keep production deployment blocked until migration backup/restore, monitoring, rate limiting, private document storage/scanning, rollback, and live acceptance tests pass.

## Release safety

- Do not change or migrate Dr. Pranali's existing live clinic data as part of SaaS onboarding work.
- Do not point staging tests at production or recovery databases.
- Do not enable public self-service clinic provisioning until email verification, abuse prevention, tenant isolation, and rollback are tested.
- Do not enable multi-clinic public booking until clinic identity is verified server-side and cross-tenant request isolation is tested.
- Do not claim the feature is live until code is tested, merged, deployed, and verified against the intended environment.
