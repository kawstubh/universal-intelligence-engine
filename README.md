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
    |
    +--> Evidence fusion / provenance / ranking
    |
    +--> Reasoning provider
    |
    +--> Evaluation
    |
    +--> Controlled learning memory
    |
    v
IntelligenceResponse
```

The same engine can serve CueScene, Dr. Pranali Dental, and future domain applications.

## Live providers

The first production adapters are intentionally replaceable:

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

Example body:

```json
{
  "goal": "Find the latest dental composite materials available in Maharashtra",
  "language": "en",
  "locale": "IN-MH",
  "context": {
    "domain": "dental"
  },
  "constraints": {
    "require_sources": true
  }
}
```

The response contains the answer, ranked evidence, confidence, actions, provider metadata, evaluation, and evidence counts.

## Configuration

Copy `.env.example` and configure:

- `OPENAI_API_KEY`
- `BRAVE_SEARCH_API_KEY`
- `UIE_API_KEY`
- `OPENAI_MODEL` (defaults to `gpt-6-luna`)
- `UIE_MEMORY_PATH`

The API fails closed for intelligence requests when `UIE_API_KEY` is missing.

## Learning and evaluation

Learning is append-only and controlled. The engine does not rewrite its own source code or silently change policy.

Every completed request can record:

- capability
- outcome
- reward/score
- goal
- locale/language
- evidence count
- evaluation result

The baseline evaluator checks response completeness and evidence grounding. This is a starting point for production evaluation, not a claim of factual correctness.

## Deployment

A Render blueprint is included in `render.yaml`. Configure the three secret environment variables in Render; do not commit them.

## Eyes and hands

UIE now has a governed sense-act runtime separating cognition from external-world I/O:

- Eyes: bounded HTTP observation with time/size limits, content hashing, provenance metadata, and structured observation results.
- Hands: explicit HTTP execution with host/method allowlists, bounded request/response sizes, idempotent retries, and human approval required for mutating methods.
- Audit: every observation/action produces a structured runtime event.
- Capability boundary: eyes and hands can be registered with the existing ToolRegistry, so domain adapters inherit the same policy boundary.

Browser/computer-control adapters can implement the same runtime boundary later without changing UIE's reasoning/planning core.

## Domain adapters

### CueScene
Uses UIE for global research, evidence, provenance, and intelligence orchestration before its video-generation pipeline.

### Dr. Pranali Dental
Uses UIE contracts for patient intelligence, clinical research, treatment research, product intelligence, supplier intelligence, practice intelligence, and referral intelligence. Clinical decisions remain under clinician control.

## Development

```bash
pip install -e '.[dev]'
pytest -q
```


## Human-controlled self-improvement governance

UIE is designed so that the intelligence layer may **propose** algorithm or policy improvements, but it cannot authorize or activate them itself.

Activation is fail-closed behind an Ed25519 human authorization gate. The engine verifies a signature over the exact change proposal, including benchmark, security, and regression results. The private signing key remains outside the engine and under the human operator's control.

Configure:
- `UIE_GOVERNANCE_PUBLIC_KEY`: base64-encoded Ed25519 public key used only for verification.

The governance boundary itself is not self-modifiable. Proposed changes should be sandboxed, benchmarked, security-tested, regression-tested, signed by the human operator, versioned, and auditable before activation.

**Important:** never place the private signing key in the repository, mobile app, or AI runtime.
