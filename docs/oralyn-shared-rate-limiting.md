# Oralyn shared rate limiting

The public guest-booking endpoint, Doctor password login, and Clinic Assistant
login use one database-backed fixed-window limiter. PostgreSQL's atomic
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING` operation makes the
counter shared across application workers and instances.

## Policies

| Scope | Window | Maximum |
|---|---:|---:|
| Public guest booking | 300 seconds | 8 |
| Doctor password login | 300 seconds | 10 |
| Clinic Assistant login | 300 seconds | 10 |

Each scope has an independent counter. The client identity is the validated
Cloudflare `CF-Connecting-IP` address on Render. Only an HMAC-SHA256
digest is persisted; raw IP addresses are not stored in the rate-limit table.

## Configuration

Set `DENTAL_RATE_LIMIT_HASH_KEY` to a stable, randomly generated secret
of at least 32 bytes in the backend service environment. It must be identical
across all instances and must not be committed to Git. The existing
`DENTAL_PUBLIC_BOOKING_HASH_KEY` is accepted as a temporary compatibility
fallback. PostgreSQL environments fail closed with HTTP 503 if neither key is
configured or if the shared table is unavailable.

A deterministic development-only key is used only when the store is SQLite.
Do not use SQLite or that fallback for deployed staging or production.

## Database

Alembic revision `0016_shared_rate_limit_buckets` creates
`dental_rate_limit_buckets`. The table stores the keyed identity digest,
fixed-window start, attempt count and update timestamp. Expired rows older than
24 hours are pruned during limiter calls.

The migration must be applied only to the approved isolated staging database
after the Render-to-Neon mapping is verified. It is not applied automatically
to any live database by this change.

## Failure behavior

The limiter never falls back to process-local counters. Missing signing key,
missing migration, or database failure returns HTTP 503 before the protected
operation proceeds. Exceeded limits return HTTP 429 and `Retry-After`.

## Acceptance before release

- Exact-head PostgreSQL migration and API tests pass.
- Two independent application instances observe the same counter.
- Restarting an instance does not reset the counter.
- Expired windows reset as expected.
- Raw IP values do not appear in stored rows or logs.
- Each endpoint retains its stable error code and existing threshold.
- Database outage and missing-key tests prove fail-closed behavior.
- Staging key is configured consistently across all instances.
