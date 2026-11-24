# Oralyn FreeLLMAPI staging integration

## Scope

This integration adds FreeLLMAPI as an optional OpenAI-compatible reasoning
provider for the Universal Intelligence Engine. It does not replace the
database, authentication, authorization, clinical safety checks, or knowledge
retrieval layer.

Patient-facing AI and patient accounts remain disabled. Doctor clinical
endpoints remain protected by Doctor authentication.

## Local Windows development

The FreeLLMAPI desktop app on the developer workstation exposes its local API
at:

    http://127.0.0.1:31415/v1

The local UI health endpoint was checked successfully; the models endpoint
requires the unified API key. Configure the local UIE process with:

    REASONING_PROVIDER=freellmapi
    FREELLMAPI_BASE_URL=http://127.0.0.1:31415/v1
    FREELLMAPI_MODEL=auto
    FREELLMAPI_API_KEY=<unified key from FreeLLMAPI Keys page>

Keep the key in a local secret store or an untracked environment file. Never
commit it, paste it into chat, add it to a URL, or put it in browser code.

## Hosted staging

A Render service cannot access the developer PC's loopback address. Hosted
staging must use a separately approved private, authenticated HTTPS tunnel or
a dedicated hosted FreeLLMAPI instance. The desktop dashboard must not be
exposed publicly. Do not configure Render to use localhost.

Before enabling hosted staging, verify the tunnel's identity controls,
TLS, allowed source service, request limits, and that only the /v1 inference
API is reachable. Do not expose the root dashboard, /v1/docs, admin, backup,
MCP, or key-management endpoints through a public tunnel.

Set FREELLMAPI_BASE_URL and FREELLMAPI_API_KEY only in the isolated staging
API service environment after the private route is provisioned. Do not change
production services or secrets.

## Patient-data boundary

FreeLLMAPI is an external inference gateway. While
REASONING_PROVIDER=freellmapi, the API rejects requests containing known
patient-identifying or patient-record context keys before inference. Doctor
patient-specific clinical AI requests are therefore blocked from this
external provider. This is intentional until a separate privacy, vendor,
retention, and clinical data-processing approval is completed.

The Doctor daily summary sends aggregate appointment counts by status only;
it does not send names, IDs, dates, treatment labels, notes, or patient-level
appointment rows. Patient-facing AI remains disabled.

## Verification

- Use synthetic test prompts only.
- Confirm /health reports freellmapi after local/staging configuration.
- Confirm the local model-list endpoint works with a unified key without
  printing the key or full provider inventory to logs.
- Verify a synthetic non-patient prompt returns a response.
- Verify patient identifiers and clinical-record context receive
  EXTERNAL_PATIENT_DATA_BLOCKED and never invoke the provider.
- Confirm Doctor clinical endpoints remain authenticated and patient-facing
  routes remain server-disabled.
- Keep all changes in a Draft PR. No production deployment or migration is
  part of this integration.
