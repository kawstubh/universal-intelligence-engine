# Universal Intelligence Engine

A domain-agnostic AI intelligence engine for research, reasoning, knowledge, agents, learning, tools, and real-world applications.

## Current architecture

```
Application
    |
    v
UIE HTTP API
    |
    +--> Knowledge providers --> Evidence
    +--> Evidence fusion / provenance / ranking
    +--> Reasoning provider
    +--> Evaluation
    +--> Controlled learning memory
    |
    v
IntelligenceResponse
```

The same engine can serve CueScene, Dr. Pranali Dental, and future domain applications.

## Dr. Pranali Dental — production stack in development

The Dental application now has a dedicated API boundary under `/v1/dental`.

```
Dental Mobile App
      |
      v
Dental API
      |
      +--> Supabase/Postgres persistence
      |
      +--> Doctor/Admin authentication
      |
      +--> Least-privilege Dental context filter
      |
      v
Universal Intelligence Engine
      |
      +--> research / reasoning / evaluation
```

Implemented on branch `feat/dental-production-stack-v1`:

- patient CRUD foundation
- appointment CRUD foundation
- FDI dental chart persistence
- Supabase/Postgres schema migrations
- doctor/admin authentication boundary using Supabase Auth
- server-only Supabase service-role access
- UIE adapter for Dental intelligence
- Dental context allow-list:
  `patient_context`, `appointments`, `dental_chart`, `screening`, `care_pathway`, `referral`, `practice`
- tests for API authentication and FDI validation
- no mobile dependency on the old `192.168.x.x:8080` LAN API

### Dental API

```
GET  /v1/dental/health
GET  /v1/dental/patients
POST /v1/dental/patients
GET  /v1/dental/appointments
POST /v1/dental/appointments
GET  /v1/dental/patients/{patient_id}/chart
POST /v1/dental/patients/{patient_id}/chart
POST /v1/dental/intelligence/run
```

Production secrets stay server-side. Never put `SUPABASE_SERVICE_ROLE_KEY`, `UIE_API_KEY`, `OPENAI_API_KEY`, or `BRAVE_SEARCH_API_KEY` in the mobile app.

## Live providers

- **BraveSearchProvider**: live web research using `BRAVE_SEARCH_API_KEY`
- **OpenAIResponsesReasoningProvider**: reasoning using `OPENAI_API_KEY` and `OPENAI_MODEL`

No credentials are stored in source control.

## HTTP API

Install:

```bash
pip install .
```

Run locally:

```bash
uvicorn universal_intelligence_engine.api:app --reload --port 8000
```

Health:

```
GET /health
```

Intelligence:

```
POST /v1/intelligence/run
Authorization: Bearer <UIE_API_KEY>
Content-Type: application/json
```

## Configuration

Use `dental.env.example` as the Dental backend environment template.

Required production backend values include:

- `DATABASE_URL` — Render PostgreSQL connection string
- `DENTAL_MASTER_CLINIC_ID`
- `DENTAL_BOOTSTRAP_ADMIN_EMAIL` and `DENTAL_BOOTSTRAP_ADMIN_PASSWORD` — optional initial admin bootstrap; use a strong password and set them in Render environment only
- `UIE_API_URL`
- `UIE_API_KEY` — server only
- `OPENAI_API_KEY` — server only
- `BRAVE_SEARCH_API_KEY` — server only

## Dental authentication

The Dental API uses clinic-issued email/password accounts; Google sign-in is not part of this flow. Passwords are stored as salted scrypt hashes. Successful login returns a random bearer session token; only its SHA-256 hash is stored in PostgreSQL. Sessions expire after 30 days and can be revoked at logout. Login attempts are rate-limited per source IP.

- `POST /v1/dental/auth/password/login` — `{ "email": "doctor@example.com", "password": "..." }`
- `GET /v1/dental/auth/me` — returns the current authenticated doctor account
- `POST /v1/dental/auth/logout` — revokes the current session
- `POST /v1/dental/auth/doctors` — admin-only account creation for the current clinic
- `POST /v1/dental/auth/doctors/{doctor_id}/deactivate` — admin-only account deactivation

To bootstrap the first administrator, set `DENTAL_BOOTSTRAP_ADMIN_EMAIL` and `DENTAL_BOOTSTRAP_ADMIN_PASSWORD` in the Render service environment. Use a unique strong password of at least 12 characters. The bootstrap is idempotent and does not overwrite an existing account. Do not put production credentials in this file or in source control.

## Database

Production schema migrations are under `alembic/versions/` and are run against the Render PostgreSQL database. Supabase migrations are retained for historical schema context and are not used for this password login flow. Existing tables are preserved by the password-auth migration.

## Safety boundary

Dental AI can organize information and prepare drafts. It does not autonomously diagnose, prescribe, order care, or choose a referral facility. Consequential clinical actions remain under clinician control.

## Learning and evaluation

Learning is append-only and controlled. The engine does not rewrite its own source code or silently change policy.

## Deployment

A Render blueprint is included in `render.yaml`. Configure secrets in the deployment environment; do not commit them.

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

## Human-controlled self-improvement governance

UIE may propose algorithm or policy improvements, but it cannot authorize or activate them itself. Activation remains behind the human authorization gate.

**Important:** never place private governance keys, service-role keys, or provider secrets in the repository, mobile app, or AI runtime.
