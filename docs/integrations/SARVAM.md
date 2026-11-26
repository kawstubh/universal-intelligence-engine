# Direct Sarvam integration (staging diagnostic)

This adapter is a temporary bypass for the Dental doctor-only AI route. It is intended to isolate Sarvam connectivity from the UIE bridge. Do not enable it in production until security, clinical quality, latency, and operational checks are complete.

## Files

- `src/universal_intelligence_engine/sarvam_adapter.py`: provider HTTP adapter, identifier redaction, response validation, and sanitized errors.
- `src/universal_intelligence_engine/dental_ai.py`: doctor route selection and response envelope.
- `tests/test_sarvam_provider.py`: unit tests for redaction, missing credentials, success, and provider authentication failure.
- `dental.env.example`: documented server-side configuration names.

## Staging configuration

Set on the staging Dental API service only:

- `DENTAL_AI_PROVIDER=sarvam`
- `SARVAM_API_KEY`: secret configured in the hosting dashboard; never commit or expose it.
- `DENTAL_SARVAM_MODEL=sarvam-105b`

The default provider selector remains `uie` when unset. Production must not receive the staging override.

## Safety boundaries

- Only doctor routes using `_doctor_call` are routed through this adapter.
- The existing doctor authentication, clinic authorization, rate limiting, cache, and audit-usage flow remain in place.
- Direct identifiers are removed recursively before the context is sent to Sarvam.
- The adapter does not write to clinical records; clinician approval remains a separate audit-only action.
- The response is an AI suggestion and must be reviewed by a qualified dentist.
- This adapter currently returns no independently verified evidence citations. The UI must not present the response as sourced or clinically validated.
- Never send real patient data during initial smoke tests. Use synthetic records.

## Rollback

Unset `DENTAL_AI_PROVIDER` or set it to `uie` on staging, then redeploy. The UIE route remains available. Do not alter production while the diagnostic is in progress.
