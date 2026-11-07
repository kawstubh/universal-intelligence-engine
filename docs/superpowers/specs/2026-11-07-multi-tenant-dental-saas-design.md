# Multi-Tenant Dental SaaS Design

**Date:** 2026-11-07  
**Status:** Approved by user

## Intent

Extend the existing Dental application into a multi-tenant SaaS platform suitable for independent dental clinics, multi-branch clinics, and hospitals with dental departments. A doctor uses one password-based identity and may switch among workspaces only where an active membership grants access.

Dr Pranali Dental Clinic remains the first tenant. Its existing patients, appointments, clinical records, doctor account, sessions, and current production behavior must be preserved.

## Goals

- Support organisations of type clinic, hospital, or group.
- Support one or more clinic workspaces/locations under an organisation, including hospital dental departments.
- Support one doctor identity with memberships in multiple workspaces.
- Keep the existing password login, bearer-token session model, and logout behavior.
- Return the doctor’s authorised workspaces at login and support explicit workspace switching.
- Scope every doctor-facing data operation to the active workspace and verify membership server-side.
- Add auditability for membership and workspace changes.
- Provide workspace selection and switching in the Doctor app.
- Keep the current Dr Pranali workspace and all its existing data intact.

## Non-goals

- No change to the Patient app.
- No patient-facing AI or patient AI endpoints.
- No changes to the UIE core or its public intelligence contract.
- No changes to Android package IDs, signing configuration, or existing app identity.
- No migration to a new authentication provider and no Google/Supabase auth changes.
- No destructive tenant data migration or production data rewrite.
- No redesign of billing lifecycle in this phase; existing clinic-scoped membership billing remains compatible.

## Approved architecture

Use the existing shared PostgreSQL database with tenant isolation enforced by the FastAPI Dental adapter and store layer. Introduce:
- `dental_organisations`: the legal/business tenant (clinic, hospital, or group).
- `dental_clinics`: operational workspaces/locations/departments belonging to an organisation. Preserve each existing `clinic_id` as the workspace ID for existing rows.
- `dental_doctor_memberships`: many-to-many doctor-to-workspace membership with a workspace role and active flag. Organisation types are `clinic`, `hospital`, and `group`; workspace types are `clinic`, `branch`, and `hospital_department`.

Keep `dental_doctors` as the global doctor identity table with its unique email and password hash. Retain its existing `clinic_id` and `role` columns as legacy/default-workspace compatibility fields; new authorization decisions use the active membership record. Keep existing session tokens and hashed-token storage. A session remains bound to one active workspace at a time; switching issues a replacement bearer token and revokes the previous token.

The migration must register every existing distinct clinic ID as a workspace and backfill a membership for every existing doctor using that doctor's current clinic ID and role. It must not move or rewrite patient, appointment, chart, periodontal, or AI event rows.

## Access and security rules

- A user may select only an active workspace with an active membership.
- Every authenticated request resolves the doctor identity and verifies the active workspace membership; the client cannot grant itself a clinic ID or role.
- Role and workspace context come from the membership row, not request payloads.
- Cross-workspace patient, appointment, chart, periodontal, AI event, staff, and billing access is denied.
- The UIE core remains domain-agnostic. Dental patient context continues to be assembled in the Dental API only after the active workspace has authorised access.
- Membership roles are `owner`, `admin`, `doctor`, or `dentist`; `owner` and `admin` can administer workspaces within their own organisation. Membership deactivation takes effect on the next authenticated request. New audit events record the active workspace ID; historical audit rows are retained.
- Never expose password hashes, token hashes, or secret environment values in API responses or logs.

## Doctor App behavior

- Existing single-workspace accounts continue to sign in and proceed without an extra selection step.
- Multi-workspace accounts receive an authorised workspace list and choose an active workspace after login.
- The app shows the active organisation/workspace and offers a switch action.
- Switching replaces the stored bearer token and user/workspace context using the existing AsyncStorage session mechanism.
- Existing package identifiers, signing, API host, and password-auth endpoint remain unchanged.

## Implementation choices left to the builder

- Use string IDs consistent with the existing schema.
- Use Alembic for additive schema changes.
- Keep tenant-aware data access in `dental_store.py` and authorization/API orchestration in `dental_auth.py` and `dental_api.py`.
- Keep Doctor app API calls in `doctor-app/src/api/doctorApi.js` and session persistence in `doctor-app/src/auth/passwordAuth.js`; add workspace UI in the existing Doctor app screen without changing app identity.
- Add backend regression tests for migration-compatible identity, membership authorization, workspace switching, and cross-tenant denial. Add focused Doctor app tests for workspace parsing/selection/switch request behavior using the test tooling already available or the smallest built-in Node test setup.
