# Dental public appointment intake

The current public appointment endpoint is a limited guest-intake flow for the configured master clinic (`DENTAL_MASTER_CLINIC_ID`, falling back to the legacy `dr-pranali` identifier). It creates a new patient and a `requested` appointment; it does not create a patient login, expose existing records, or invoke AI.

## Burst protection

The endpoint enforces an in-process sliding-window limit of eight requests per client IP per five minutes, returns HTTP 429 with a `Retry-After` header when exceeded, and bounds the number of tracked client keys. This is a temporary single-instance safeguard, not a distributed rate limiter.

Before running multiple API instances or enabling broad public guest booking, configure an edge/WAF or shared rate-limiting service. Do not rely on the in-process counter across replicas or restarts.

## Multi-clinic public booking

The current guest form has no public clinic slug or clinic selector and therefore targets only the configured master clinic. Do not use this endpoint as a multi-tenant clinic directory. A future multi-clinic public booking flow must resolve a clinic through a verified, non-secret public slug and apply per-clinic abuse controls before creating patient rows.
