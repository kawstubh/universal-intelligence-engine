# Dental SaaS production rollout verification

- Multi-tenant API integration merged to the production deployment branch.
- Existing production database target and patient records are preserved; no production database URL was changed and no production migration was executed.
- Existing production database schema is expected at Alembic revision `0007_dental_audit_workspace`.
- Dedicated staging database `dental_staging` is separate, empty of patient records, and migrated to `0007_dental_audit_workspace`.
- Staging `/health` and `/ready` return HTTP 200; workspace and organisation routes are present and enforce authentication.
- Release gate: verify production deployment commit and repeat health, readiness, and authenticated doctor workflow checks after deployment.
