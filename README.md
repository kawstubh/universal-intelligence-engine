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

The response contains the answer, ranked evidence, confidence, actions, provider metadata, evaluation, and evidence counts.

## Configuration

- `OPENAI_API_KEY`
- `BRAVE_SEARCH_API_KEY`
- `UIE_API_KEY`
- `OPENAI_MODEL`
- `UIE_MEMORY_PATH`
- `UIE_GOVERNANCE_PUBLIC_KEY`

The API fails closed for intelligence requests when `UIE_API_KEY` is missing.

## Controlled self-improvement

UIE now provides a **full-access candidate improvement runtime**.

An improvement candidate can, inside a disposable workspace:

1. inspect the complete source tree;
2. add, modify, or delete source files;
3. apply a patch;
4. compile the complete project;
5. run the complete test suite;
6. inspect the complete resulting diff;
7. run policy and static safety gates;
8. return a scored candidate for governance review.

The disposable workspace deliberately contains no repository remote, production checkout, environment secrets, or credential directories. Network access is disabled by contract and should additionally be enforced by the CI/container runtime.

**Full access means full source-development capability inside the sandbox, not unrestricted production authority.** UIE still cannot silently commit, deploy, change governance, or bypass the Ed25519 human authorization gate.

The production promotion chain remains:

`propose -> sandbox edit -> compile -> tests -> security -> policy -> benchmark -> human signature -> explicit promotion`

This separation is intentional: the intelligence can improve itself aggressively while production authority remains human-controlled.

## Learning and evaluation

Learning is append-only and controlled. The engine does not silently rewrite production source code or policy.

Every completed request can record capability, outcome, reward/score, goal, locale/language, evidence count, and evaluation result.

The baseline evaluator checks response completeness and evidence grounding. It is not a claim of factual correctness.

## Deployment

A Render blueprint is included in `render.yaml`. Configure secrets in Render; do not commit them.

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

## Human-controlled governance

UIE is designed so that the intelligence layer may **propose** algorithm or policy improvements, but it cannot authorize or activate them itself.

Activation is fail-closed behind an Ed25519 human authorization gate. The engine verifies a signature over the exact change proposal, including benchmark, security, and regression results. The private signing key remains outside the engine and under the human operator's control.

**Important:** never place the private signing key in the repository, mobile app, or AI runtime.
