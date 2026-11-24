# Oralyn + FreeLLMAPI — isolated staging integration

## Purpose

FreeLLMAPI is an optional OpenAI-compatible gateway for development and synthetic integration tests. Its upstream README describes the project as personal experimentation, not production infrastructure. Free-tier quotas have no SLA, may be exhausted, and vary by provider.

## Hard safety boundary

- This integration is disabled by default.
- The only Oralyn route is POST /v1/dental/ai/doctor/provider-smoke-test.
- It requires a Doctor-authenticated session.
- It accepts no request body and reads no patient, appointment, chart, scan, note, or clinic record.
- The UIE provider enforces one fixed prompt and one fixed synthetic context.
- Any other request routed to FreeLLMAPI is rejected before network I/O.
- Do not enable patient-data forwarding or clinical decision support through free-tier providers.
- Do not deploy this configuration to production.

## Local FreeLLMAPI desktop settings

The desktop gateway defaults to http://127.0.0.1:31415/v1.

Configure the local UIE process with these environment values:

REASONING_PROVIDER=freellmapi
REASONING_MODEL=auto:balanced
REASONING_BASE_URL=http://127.0.0.1:31415/v1
FREELLMAPI_API_KEY=<unified-key-from-local-FreeLLMAPI-Keys-page>
FREELLMAPI_SMOKE_TEST_ENABLED=true

Store the unified key only in an ignored local environment file or secret manager. Never put it in source control, a mobile app, browser JavaScript, logs, or chat. The key is entered directly in the FreeLLMAPI application; do not paste it into ChatGPT.

## Doctor-only smoke test

With the isolated staging API and UIE configured, an authenticated Doctor can call:

POST /v1/dental/ai/doctor/provider-smoke-test
Authorization: Bearer <doctor-session>

Expected successful response contains ORALYN-FREELLMAPI-OK. A 404 means the feature flag is disabled. A 503/502 indicates gateway or UIE configuration/connectivity failure.

## Remote staging limitation

A desktop gateway bound to 127.0.0.1 is reachable only from the same machine. A Render service cannot reach a developer laptop's loopback address. Do not solve this by exposing the whole desktop gateway publicly or enabling unrestricted Tailscale Serve. A remote staging gateway needs a separately reviewed, authenticated private-network or hosted deployment design, with access control and provider terms reviewed first.

## Acceptance checklist

- [ ] Provider keys are entered directly into FreeLLMAPI; never shared in chat.
- [ ] Only provider keys for legitimate free tiers are used; no quota evasion or duplicate-account abuse.
- [ ] Local gateway API key is stored outside the repository.
- [ ] Doctor authentication is required for the Oralyn smoke-test route.
- [ ] Patient/assistant callers cannot invoke the route.
- [ ] Exact synthetic prompt is the only payload accepted by the FreeLLMAPI provider.
- [ ] Tests verify the provider rejects patient-context requests before network I/O.
- [ ] No production deployment, patient data, or paid infrastructure is involved.
