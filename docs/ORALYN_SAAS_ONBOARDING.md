# Oralyn SaaS onboarding and entitlement specification

Status: approved product requirements; implementation in progress.
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

All clinic-owned data, files, patient portals, CRM records, staff memberships, audit events, and billing records must be scoped to the clinic. Backend authorization is authoritative; UI-only filtering is not sufficient.

Patient portal accounts are clinic-bound and patient-bound. A patient must never be able to switch clinic context or retrieve another patient's records.

## Website demo request

The public Oralyn website collects only necessary business contact and clinic details. A successful request:
1. validates and rate-limits the request;
2. creates or safely reuses a pending onboarding request;
3. provisions a clinic workspace and owner login only after the required verification step is satisfied;
4. records `trial_started_at` and `trial_ends_at = trial_started_at + 72 hours`;
5. sends a secure account-activation link (never emails a plaintext password);
6. shows the demo end date/time and how to subscribe.

Repeated submissions must not create multiple workspaces or restart an existing trial. Email/phone verification and abuse controls are required before public self-service provisioning is enabled.

## Master administration

The Oralyn master admin console must show clinics, owner/contact, onboarding state, trial start/end, entitlement, plan, payment status, renewal, cancellation and audit history. Master admin operational access must not imply unrestricted access to clinical records.

Dr. Pranali's founding entitlement must be assigned by a protected server-side configuration/seed or explicit admin operation, not by a user-editable clinic name or request field.

## Existing implementation discovered during preparation

- Backend already has clinic/workspace membership and doctor password authentication.
- Backend already exposes plan listing, membership lookup, Razorpay order creation, and a Razorpay webhook.
- Existing billing currently uses a hard-coded master-clinic ID check for the free account and does not yet implement a 72-hour trial lifecycle or public website self-service provisioning.
- Existing plan prices are ₹4,999/month (Clinic) and ₹9,999/month (Enterprise); these are existing code values, not a newly approved pricing decision.
- Current patient journey in the web portal is public account-free appointment requests; patient accounts/login are not yet part of the portal release.
- The current desktop folder is a React/Vite browser/PWA portal, not a packaged Windows desktop application.

## Required implementation sequence

1. Add migration-backed trial and entitlement fields/tables, with a non-expiring founding-clinic entitlement.
2. Implement secure, idempotent clinic provisioning and owner activation; add server-side trial status/expiry enforcement.
3. Harden Razorpay order/webhook mapping and ensure paid entitlement is applied only to the matching clinic.
4. Add public demo-request and owner activation flow to the Oralyn marketing website.
5. Build Master Admin clinic, trial, subscription and audit management.
6. Build clinic-bound patient portal authentication and patient-owned record access.
7. Complete clinic CRM and clinic branding/configuration.
8. Add tests for duplicate submissions, expiry boundaries, founding-clinic immunity, payment webhook replay, cross-tenant access, patient-to-patient isolation, and account suspension.
9. Keep production deployment blocked until migration backup/restore, monitoring, rate limiting, private document storage/scanning, rollback, and live acceptance tests pass.

## Release safety

- Do not change or migrate Dr. Pranali's existing live clinic data as part of SaaS onboarding work.
- Do not point staging tests at production or recovery databases.
- Do not enable public self-service clinic provisioning until email verification, abuse prevention, tenant isolation, and rollback are tested.
- Do not claim the feature is live until code is tested, merged, deployed, and verified against the intended environment.
