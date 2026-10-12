# UIE + Dental Cycle Status

Last cycle: 2026-10-13; GitHub Actions timestamps observed during the cycle: 2026-10-12 21:31–21:32 UTC.

## Code changed this cycle
- Ported deterministic native reasoning from PR #13 into `src/universal_intelligence_engine/reasoning.py`, plus temporal contradiction/freshness assessment.
- Added `NoKnowledgeProvider`; the API selects native reasoning when `OPENAI_API_KEY` is absent and does not fabricate evidence.
- Added engine behavior: when `constraints.require_sources` is true and no evidence exists, reasoning is not called and an explicit evidence-unavailable result is returned.
- Ported the bounded planner and agent modules from PR #13, including approval-required action stops and rejection of over-budget plans; added planner/agent tests and exports.
- Added native fallback and required-evidence tests.
- Added an idempotence test proving bootstrap does not recreate an existing admin.
- Normalized API version reporting to the package version.
- Existing cross-clinic Doctor AI denial test remains in `tests/test_dental_e2e.py` and asserts 403 / `PATIENT_CLINIC_ACCESS_DENIED`.
- PR #17: https://github.com/kawstubh/universal-intelligence-engine/pull/17
- Mobile roadmap PR #9: https://github.com/kawstubh/dr-pranali-dental-mobile-v2/pull/9

## CI findings
- PR #17's prior required-gates runs failed in Ruff, not pytest or Gitleaks. The actual Ruff log reports 89 findings across existing source/tests: import sorting, unused imports, FastAPI `Depends` B008, modern typing/date aliases, mutable class defaults, a duplicate `create_membership_order` definition (F811), and other style/static findings. The gate was not weakened and no tests were skipped.
- On the failed run, unit/safety passed and Gitleaks passed; Ruff failed and mypy was skipped as a consequence of the failed step.
- After this cycle's native/planner/agent commits, new CI runs are queued/in progress: https://github.com/kawstubh/universal-intelligence-engine/actions. No final green result has been verified for the latest SHA yet.
- Mobile PR #9 has no PR-triggered workflow run in the accessible Actions listing. The latest unrelated Doctor APK run on `hardening/launch-safety` passed, but that is not a test result for PR #9. Documentation-only PR changes did not trigger a mobile CI run.

## Repository settings verification
The connected GitHub API interface rejected reads of protected repository settings/Actions permissions endpoints, so the following settings are **not verified** from this cycle:
- Allow auto-merge: https://github.com/kawstubh/universal-intelligence-engine/settings
- Branch protection/rules for `integration/uie-dental`: https://github.com/kawstubh/universal-intelligence-engine/settings/branches
- Actions permissions: https://github.com/kawstubh/universal-intelligence-engine/settings/actions
- Required status checks should include `unit-and-safety`, `type-and-lint`, and `gitleaks`; do not enable auto-merge until these checks are required and green.
- Mobile repo equivalent settings: https://github.com/kawstubh/dr-pranali-dental-mobile-v2/settings/branches and https://github.com/kawstubh/dr-pranali-dental-mobile-v2/settings/actions

## Remaining blockers
- The Ruff failure requires actual code cleanup across multiple files, including fixing the duplicate billing function; it has not yet been fully repaired.
- First-run bootstrap is tested for idempotence, but a dedicated `DENTAL_SETUP_TOKEN`-protected setup endpoint does not currently exist. This cycle did not add an unauthenticated setup endpoint.
- Full M1 promotion remains blocked until latest required checks pass and branch-protection settings are verified.
- Post-deploy smoke is currently manual-dispatch only and has not been wired to a verified Render deploy event. No live smoke or deployment was performed.
- No Render settings, `DATABASE_URL`, package IDs, signing, OAuth, master, or CueScene were changed.

## Next step
Fix Ruff findings at source, rerun all checks, add a dedicated cross-clinic/setup-lock security regression where not already covered, verify repository settings in GitHub UI/API, and only then enable auto-merge/promotion. Do not deploy while checks are pending or failing.

## Safety state
- `UIE_LEARNING_ENABLED` remains OFF by default; no activation performed.
- Patient AI server guard remains HTTP 404 and hidden from OpenAPI.
- No production deployment triggered.
