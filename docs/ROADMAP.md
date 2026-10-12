# UIE + Dental Delivery Roadmap

Branch: `integration/uie-dental`  
Deploy branch: `feat/dental-production-stack-v1`  
Policy: no direct pushes to deploy branch or `master`; no clinical-behaviour changes without flags; no secrets in source. Each milestone is complete only when its acceptance tests pass and the live smoke evidence is recorded.

## M0 — Doctor session and access boundary
- [ ] Verify Doctor email/password login issues a hashed, expiring session token and logout revokes it.
- [ ] Verify `require_doctor` rejects missing, invalid, expired, revoked, inactive, wrong-role, and cross-clinic sessions.
- [ ] Verify first-run admin setup is unavailable once an admin exists; setup token must never be logged or stored in source.
- [ ] Verify login abuse protection and audit events; do not weaken existing auth while implementing persistence.
- [ ] Acceptance: security regression suite passes against a temporary database; unauthenticated Doctor AI returns 401; cross-clinic requests return 403.

## M1 — Integrate #13 and #16; deploy safely
- [ ] Port PR #13 API contract hardening, constant-time API-key comparison, provider-offline behavior, approval gates, and plan-budget rejection onto this branch without overwriting Dental-specific changes.
- [ ] Port PR #16 patient-AI server-side 404 guard and OpenAPI hiding.
- [ ] Add required CI gates: unit, golden dental safety, security regression, typing/lint, secret scan.
- [ ] Add live post-deploy smoke: `/health`, `/v1/dental/health`, patient AI 404, unauthenticated UIE AI 401, and safe provider-unavailable/native-fallback behavior.
- [ ] Acceptance: every required check green, smoke output retained, rollback commit recorded. Promote only through an auto-merging PR to the deploy branch.

## M2 — Provider chain and durable state
- [ ] Implement Groq → explicitly configured fallback provider → native reasoning → graceful unavailable response.
- [ ] Enforce timeouts, bounded retries/backoff, request caps, safe caching, and provider error classification.
- [ ] Validate all model outputs against strict Pydantic/JSON schemas; reject or bounded-retry invalid output.
- [ ] Add prompt-injection boundaries; treat patient records and retrieved text as untrusted data.
- [ ] Move UIE learning, cache, usage, and approval state from local files/temp storage to additive PostgreSQL migrations; never change `DATABASE_URL`.
- [ ] Add mocked-provider, schema-invalid, timeout, retry, cache, migration, and restart-persistence tests.
- [ ] Acceptance: no fake evidence; no unvalidated model JSON reaches consumers; native fallback is deterministic and labelled.

## M3 — Doctor AI capabilities
- [ ] Patient summary and risk flags.
- [ ] Treatment-plan suggestions.
- [ ] Chart and periodontogram insights.
- [ ] Clinical-note drafting.
- [ ] Follow-up/recall suggestions and daily Doctor summary.
- [ ] Missing scan-analysis credentials return an explicit “not configured” result.
- [ ] Every result is labelled **“AI suggestion, doctor to verify”**; no dosage advice.
- [ ] No AI endpoint may create/update/delete clinical records; doctor must apply edits explicitly.
- [ ] Store accept/edit/dismiss decisions with doctor, timestamp, and final edited content.
- [ ] Acceptance: golden safety suite, no-write tests, emergency flagging, and cross-clinic denial tests pass.

## M4 — Guarded learning
- [ ] `UIE_LEARNING_ENABLED` defaults to `OFF`; final activation remains an operator decision.
- [ ] Learn only from explicit doctor accept/edit/dismiss and optional feedback.
- [ ] Scope memory by clinic/doctor; version learned artifacts and support admin view, disable, and rollback.
- [ ] Aggregate common patterns only without patient identifiers or clinical free text.
- [ ] Golden evaluation set blocks regressions; tests prove disabled-by-default behavior.
- [ ] Acceptance: learning-off tests pass and no clinical behavior changes without an explicit flag.

## M5 — Doctor app wiring and APK validation
- [ ] Wire Doctor app screens to the authenticated Doctor endpoints on a feature branch.
- [ ] Keep package IDs, signing configuration, signing keys, and Google OAuth clients unchanged.
- [ ] Run TypeScript checks, lint, Expo export, and existing APK workflows.
- [ ] Build APKs only when mobile paths change; cap at four APK builds per UTC day.
- [ ] Release publishing is master-only; this automation must never merge to master.
- [ ] Acceptance: install/test script verifies sign-in, logout, cross-clinic denial, doctor-only AI, error states, and “AI suggestion, doctor to verify” labels.

## Option D redesign groups
- [ ] D1 Foundation: tokens, shared layout, accessibility, loading/error/empty states.
- [ ] D2 Doctor home: daily summary, appointments, urgent/emergency flags.
- [ ] D3 Patient workspace: chart, periodontogram, history and treatment context.
- [ ] D4 AI workspace: summary, suggestions, note drafting, accept/edit/dismiss.
- [ ] D5 Follow-up: recall, reminders, and daily workflow.
- [ ] D6 QA and release: responsive layouts, offline/error handling, regression screenshots and APK verification.
- [ ] Acceptance: each group has reviewable screenshots and tests; clinical content is advisory only.

## Staged integration of open branches
- [ ] Stage 1: PR #13 and PR #16 ported to `integration/uie-dental`; tests prove both sets of behavior.
- [ ] Stage 2: provider/state changes on separate feature branch and PR into integration.
- [ ] Stage 3: Doctor capability work on separate feature branch and PR into integration.
- [ ] Stage 4: guarded learning work on separate feature branch and PR into integration.
- [ ] Stage 5: mobile wiring and Option D groups on mobile feature branches and PRs.
- [ ] Stage 6: promote verified integration to `feat/dental-production-stack-v1` only through a gated PR; Render deploy followed by live smoke.
- [ ] Any failed smoke blocks promotion, triggers rollback to last known-good deploy commit, and creates an issue with the failure log. Fix forward on a feature branch.

## Non-negotiable invariants
- [ ] AI never writes clinical records autonomously.
- [ ] All clinical AI output says “AI suggestion, doctor to verify”.
- [ ] No medication dosage advice.
- [ ] Access control and cross-clinic isolation are enforced server-side.
- [ ] Emergency flags are preserved and tested.
- [ ] Patient-facing AI routes return 404 at launch.
- [ ] No secrets, bypass/demo auth paths, skipped tests, weakened tests, or `|| true`.
