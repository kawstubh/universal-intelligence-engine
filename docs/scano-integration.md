# scanO integration boundary

This project exposes a provider boundary for scanO screening data without coupling the Dental Intelligence Engine to undocumented provider fields.

## Flow

scanO screening/report or webhook
→ `POST /v1/dental/integrations/scano/screening`
→ structured screening persistence
→ audit trail
→ Dental Intelligence Engine

## Current contract

Authentication uses:

`Authorization: Bearer <SCANO_INTEGRATION_KEY>`

The request envelope accepts:

- `external_id`: provider event/result identifier
- `patient_id`: our patient identifier when already mapped
- `screening_id`: provider screening identifier
- `occurred_at`
- `risk_score`
- `findings[]`
- `evidence[]`
- `raw_result`

## Important

The public scanO API documentation currently states that detailed endpoint schemas are provided after API access approval. Therefore this adapter deliberately keeps the official provider payload in `raw_result` until the scanO sandbox/schema is available.

This endpoint only ingests and stores screening information. It does not autonomously diagnose, prescribe, treat, or refer.

## Next integration step

When scanO provides sandbox/API credentials and its official result/webhook schema:

1. Map the official scanO payload into this envelope.
2. Add signature verification if scanO requires webhook signing.
3. Add idempotency handling using the provider event/result ID.
4. Add consent/patient-identity mapping rules.
5. Feed the normalized screening context into clinician-reviewed Dental Intelligence workflows.
