# Multi-Tenant Dental SaaS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add organisation/workspace tenancy and multi-workspace doctor access to the Dental backend and Doctor app while preserving Dr Pranali production data and current authentication.

**Architecture:** Extend the shared PostgreSQL schema with organisations, clinic workspaces, and doctor memberships. Keep the existing doctor identity and hashed bearer-session mechanism; each session is bound to one active workspace and workspace switching rotates the session. All authorization remains server-side and all Dental data continues to be scoped by the active workspace.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/Alembic, PostgreSQL/psycopg, pytest; React Native, Expo, AsyncStorage, JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-07-multi-tenant-dental-saas-design.md`

## Global Constraints

- Preserve existing Dr Pranali data and current authentication.
- Use shared PostgreSQL with strict workspace isolation.
- A doctor can access only workspaces with active memberships.
- Do not change Patient app behavior or enable patient-facing AI.
- Do not modify UIE core.
- Do not change Android package IDs, signing configuration, or app identity.
- Do not rewrite existing clinical rows during tenant migration.
- Keep existing single-workspace login behavior compatible.
- Keep current clinic-scoped billing behavior; do not redesign billing lifecycle in this phase.

## Review Focus

- A doctor whose membership is revoked while a session is active must lose access on the next request.
- A doctor with two workspaces must not reuse a stale token to access the previous workspace after switching.
- A legacy doctor/session with no explicit workspace selection must continue to resolve to its migrated legacy workspace.
- A workspace ID supplied by a client without membership must never change the authenticated tenant context.
- Two clinics with the same patient ID value must not cause cross-tenant chart or appointment access.

---

### Task 1: Add tenant registry and membership schema

**Files:**
- Create: `alembic/versions/0006_multi_tenant_workspaces.py`
- Test: `tests/test_dental_multi_tenant_migration.py`
- Modify: none

**Interfaces:**
- Produces tables `dental_organisations`, `dental_clinics`, and `dental_doctor_memberships`.
- Organisation fields: `id`, `name`, `organisation_type`, `active`, `created_at`.
- Clinic fields: `id`, `organisation_id`, `name`, `clinic_type`, `parent_clinic_id`, `active`, `created_at`.
- Membership fields: `id`, `doctor_id`, `clinic_id`, `role`, `active`, `created_at`; unique constraint on (`doctor_id`, `clinic_id`). Allowed roles: `owner`, `admin`, `doctor`, `dentist`.

- [ ] **Step 1: Write migration tests** asserting that every distinct legacy clinic ID is represented by one workspace, every existing doctor receives a membership matching its legacy clinic and role, and patient/appointment/chart row counts and IDs remain unchanged.
- [ ] **Step 2: Run the migration tests and verify they fail** because the registry and membership tables do not exist.
- [ ] **Step 3: Implement additive migration** with idempotent helpers matching the existing Alembic migration style. Preserve each existing `clinic_id` as the workspace ID. Create a corresponding organisation ID and seed descriptive names from the configured master clinic where available; use a safe legacy label otherwise.
- [ ] **Step 4: Run migration tests** and verify data identity/count assertions pass.
- [ ] **Step 5: Commit** as `feat: add dental organisation and workspace schema`.

### Task 2: Add tenant and membership persistence methods

**Files:**
- Modify: `src/universal_intelligence_engine/dental_store.py`
- Test: `tests/test_dental_multi_tenant_store.py`

**Interfaces:**
- `organisations_for_doctor(doctor_id: str) -> list[dict[str, Any]]`
- `workspaces_for_doctor(doctor_id: str) -> list[dict[str, Any]]`
- `doctor_membership(doctor_id: str, clinic_id: str) -> dict[str, Any] | None`
- `create_organisation(data: dict[str, Any]) -> dict[str, Any]`
- `create_clinic(data: dict[str, Any]) -> dict[str, Any]`
- `create_doctor_membership(doctor_id: str, clinic_id: str, role: str) -> dict[str, Any]`
- `set_doctor_membership_active(doctor_id: str, clinic_id: str, active: bool) -> None`

- [ ] **Step 1: Write tests** for listing only active memberships, retrieving a single doctor/workspace membership, creating organisations/workspaces/memberships, and deactivating a membership.
- [ ] **Step 2: Run the tests and verify the expected missing-method failures.**
- [ ] **Step 3: Implement the methods** using the store's existing PostgreSQL/SQLite placeholder conventions and transaction patterns.
- [ ] **Step 4: Run the store tests and existing store/API tests.**
- [ ] **Step 5: Commit** as `feat: add dental workspace membership persistence`.

### Task 3: Make password sessions workspace-aware

**Files:**
- Modify: `src/universal_intelligence_engine/dental_auth.py`
- Modify: `src/universal_intelligence_engine/dental_store.py`
- Test: `tests/test_dental_multi_tenant_auth.py`
- Regression test: `tests/test_dental_password_auth.py`

**Interfaces:**
- Login response retains `access_token`, `token_type`, `expires_in`, and `user`; adds `workspaces` with authorised active workspace metadata.
- `POST /v1/dental/auth/workspace/switch` accepts `clinic_id` and returns a replacement `access_token`, active user context, and available workspaces.
- `require_doctor` continues returning `id`, `user_id`, `email`, `clinic_id`, and `role`, deriving role from the active membership. It accepts `owner`, `admin`, `doctor`, and `dentist`; owner/admin checks are enforced within the actor's organisation.
- Use the existing `DentalStore.revoke_session(token_hash: str) -> None` and hashed-token storage.

- [ ] **Step 1: Write tests** for multi-workspace login response, single-workspace backward compatibility, successful switch, rejection of non-member workspace, revocation of the previous token, and immediate denial after membership deactivation.
- [ ] **Step 2: Run tests and verify they fail for the expected missing workspace behavior.**
- [ ] **Step 3: Implement workspace-aware login and session resolution.** Keep scrypt password verification, token generation, token hashing, expiry, rate limiting, and logout semantics. Workspace switch must verify membership, create a new hashed session, and revoke the old session.
- [ ] **Step 4: Run authentication and existing password-auth tests.**
- [ ] **Step 5: Commit** as `feat: support multi-workspace doctor sessions`.

### Task 4: Add organisation and membership API operations

**Files:**
- Modify: `src/universal_intelligence_engine/dental_api.py`
- Modify: `src/universal_intelligence_engine/dental_store.py` only if a missing persistence operation is identified
- Test: `tests/test_dental_multi_tenant_api.py`
- Regression test: `tests/test_dental_api.py`

**Interfaces:**
- `GET /v1/dental/auth/workspaces` returns the authenticated doctor's active workspaces.
- `POST /v1/dental/organisations` creates an organisation and its first workspace for an authenticated clinic administrator; the actor receives owner membership in the new workspace.
- `POST /v1/dental/organisations/{organisation_id}/workspaces` creates another workspace only for an owner/admin already belonging to that organisation.
- `POST /v1/dental/workspaces/{clinic_id}/memberships` links an existing doctor identity to a workspace only when the actor has owner/admin permission in that organisation.
- `PATCH /v1/dental/workspaces/{clinic_id}/memberships/{doctor_id}` activates/deactivates an existing membership under the same organisation.
- All request models validate organisation type, clinic type, role and bounded text lengths. Owner/admin permissions are checked within the same organisation. Client-supplied role/clinic context never overrides the authenticated actor.

- [ ] **Step 1: Write tests** for authorised organisation creation, branch/workspace creation, membership assignment, membership deactivation, and 403/404 responses for cross-organisation administration.
- [ ] **Step 2: Run tests and verify the expected missing-route failures.**
- [ ] **Step 3: Implement routes** with explicit same-organisation checks and audit events; do not expose password hashes or token hashes.
- [ ] **Step 4: Run the full backend suite** including existing cross-clinic patient access and UIE doctor-only tests.
- [ ] **Step 5: Commit** as `feat: add dental organisation and workspace APIs`.

### Task 5: Add Doctor app workspace selection and switching

**Files:**
- Create: `doctor-app/src/auth/workspaceContext.js` (pure workspace normalization/default-selection/session-persistence helpers)
- Create: `doctor-app/src/auth/workspaceContext.test.cjs` (Node built-in tests)
- Modify: `doctor-app/src/api/doctorApi.js`
- Modify: `doctor-app/src/auth/passwordAuth.js`
- Modify: `doctor-app/App.js`
- Modify: `doctor-app/package.json` only if a built-in test script is needed
- Test: add focused Node built-in tests for workspace response parsing and switch-token persistence; do not add a third-party test framework unless the repository already has one.

**Interfaces:**
- Add API helper `listDoctorWorkspaces(token)`.
- Add API helper `switchDoctorWorkspace(token, clinicId)`.
- Persist the replacement token and active user using the existing `doctor_session` and `doctor_user` AsyncStorage keys.
- Expose the active workspace and workspace list to the Doctor app screen.

- [ ] **Step 1: Write `workspaceContext.test.cjs` tests** for single-workspace default selection, multi-workspace list normalization, successful session persistence, and failed switch leaving the existing stored session unchanged.
- [ ] **Step 2: Run tests and verify the missing helper/behavior failures.**
- [ ] **Step 3: Implement `normalizeWorkspaces(workspaces)`, `selectDefaultWorkspace(workspaces, defaultClinicId)`, and `persistWorkspaceSession(storage, session)` in `workspaceContext.js`; implement API helpers and use the pure persistence helper with AsyncStorage. Do not change the API base URL, app identifiers, package ID, EAS project ID, or signing configuration.
- [ ] **Step 4: Add a workspace selector** that is skipped for a single workspace and available from the Doctor app header/menu when multiple workspaces exist. Show organisation and workspace names; reload workspace-scoped patients/appointments after a successful switch.
- [ ] **Step 5: Run mobile tests and Expo Android export/type checks available in the repository.**
- [ ] **Step 6: Commit** as `feat: add doctor workspace switcher`.

### Task 6: End-to-end tenant isolation and migration verification

**Files:**
- Modify: `tests/test_dental_multi_tenant_api.py`
- Modify: `tests/test_dental_multi_tenant_auth.py`
- Add: `docs/superpowers/verification/multi-tenant-dental-saas-checklist.md`

**Interfaces:**
- Verification checklist covers two independent clinics, one hospital with two dental workspaces, a doctor with memberships in two workspaces, and an unrelated doctor with no membership.

- [ ] **Step 1: Add integration tests** that create two organisations and multiple workspaces, create patients with overlapping identifiers in separate workspaces, and verify no cross-tenant reads or writes are possible for patients, appointments, charts, periodontal records, and UIE context.
- [ ] **Step 2: Run tests and verify any uncovered isolation path fails before the final hardening change.**
- [ ] **Step 3: Add the minimal tenant guard or scoped store condition required by each failing test.**
- [ ] **Step 4: Run the complete backend test suite and Doctor app test/export commands.**
- [ ] **Step 5: Commit** as `test: verify dental multi-tenant isolation`.

## Completion Criteria

- Existing Dr Pranali clinic IDs and clinical row IDs/counts are unchanged after migration.
- Existing single-workspace password login and logout tests remain green.
- Multi-workspace doctors can switch workspaces with token rotation.
- Inactive or absent memberships cannot access a workspace.
- All doctor API data access remains scoped to the active workspace.
- Doctor app displays and switches authorised workspaces.
- Patient app, UIE core, package IDs, and signing configuration are unchanged.
