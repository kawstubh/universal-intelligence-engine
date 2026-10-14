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

- **FreePublicKnowledgeProvider**: keyless, best-effort retrieval from Wikipedia's public API; medical and dental queries also search Europe PMC's public biomedical literature API.
- These public sources do not require a paid search subscription or API key. They are not broad web search; availability and evidence quality vary by query, and the engine must not invent evidence when results are empty.
- **OpenAIResponsesReasoningProvider**: optional reasoning using `OPENAI_API_KEY` and `OPENAI_MODEL`; native reasoning is used when no OpenAI key is configured.

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

- `UIE_API_KEY` (required to authorize API requests)
- `OPENAI_API_KEY` (optional; native reasoning works without it)
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

A Render blueprint is included in `render.yaml`. Configure `UIE_API_KEY` in Render; `OPENAI_API_KEY` is optional. Do not commit secrets.

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

## Public web knowledge harvesting (initial implementation)

UIE can now persist text from explicitly submitted public URLs on an approved
domain allowlist, subject to each site's robots.txt. The stored corpus records
the canonical URL, source domain, retrieval timestamp, HTTP status, content type,
and SHA-256 content digest. Queries search this local corpus before the existing
Wikipedia and Europe PMC retrieval paths.

Configuration:
- UIE_WEB_KNOWLEDGE_PATH — SQLite corpus path (default .data/web_knowledge.sqlite3).
- UIE_API_KEY — required for API endpoints, including harvesting and stats.

Authorized endpoints:
- POST /v1/knowledge/harvest with {"urls":["https://docs.python.org/3/library/sqlite3.html"]} (maximum 10 URLs per request).
- GET /v1/knowledge/stats.

This is deliberately a bounded first milestone, not an unrestricted crawler or
a claim to know the whole internet. It does not bypass logins/paywalls, ignores
non-HTML resources, respects robots rules (fails closed if robots cannot be read),
uses a domain allowlist, and limits each response to 1.5 MB and extracted text to
80,000 characters. Retrieval is lexical, not semantic/vector search. Harvesting
is on-demand, not yet scheduled; no model weights are trained. SQLite at Render's
default ephemeral filesystem is not production-durable. Before production use,
configure a persistent disk or move this corpus to a dedicated PostgreSQL/vector
store and implement a scheduled, rate-limited discovery pipeline with monitoring,
retention, source licensing, and content-quality controls.

### Bounded cross-domain discovery queue

The initial seven-category seed catalog is in `src/universal_intelligence_engine/config/seeds.json`. The authenticated queue endpoints allow an operator to seed the queue, discover a bounded number of approved URLs from sitemap/RSS/Atom-style XML sources, and process a small batch at a time:

- `POST /v1/knowledge/queue/seed`
- `POST /v1/knowledge/queue/discover?max_sources=3&max_urls_per_source=20`
- `POST /v1/knowledge/queue/run?limit=5`

The queue deduplicates URLs and tags each queued item with a category. Each discovery/run call is bounded; this is **operator-triggered**, not a background scheduler. Sitemap/feed access also checks the source allowlist and robots.txt; discovered page URLs must independently pass the allowlist. The worker uses the existing corpus fetch policy and does not write scraped text into validated lesson memory. Seed endpoints and discovery do not mean every listed source is available or that any pages have been harvested yet. Configure durable storage and a real scheduler only after staging validation.


### Crawl safety hardening and API tests

The bounded worker validates standard HTTPS URLs (including URL length and port), ignores XML that is not a recognized sitemap/feed root, and retries transient fetch/robots failures a maximum of three attempts before marking an item skipped. Authentication checks for knowledge endpoints are covered by API-level tests. The worker remains operator-triggered; no scheduler is configured.


### SSRF and redirect protections

Crawler outbound requests now pre-resolve hostnames and reject non-public/mixed DNS answers, including loopback, RFC1918 private, link-local/cloud metadata, and IPv6 local/private ranges. Redirects are validated before each hop and must remain on the approved domain allowlist. Crawler and robots requests use a five-second timeout. HTTP content is capped at 2,000,000 bytes (including an early Content-Length check and a bounded read). See `tests/test_ingestion_security.py` for adversarial resolver and redirect coverage. DNS checks reduce SSRF/DNS-rebinding risk; for high assurance, deploy outbound egress filtering too, since a conventional urllib client cannot pin the already-validated DNS address to the socket across a separate resolution without a custom transport.


### Claim distillation and contradiction tracking

When a queue item is successfully stored, UIE now loads its extracted text, applies conservative rule-based sentence distillation, and writes structured claims into `UIE_CLAIM_MEMORY_PATH` (default `.data/claim_memory.sqlite3`). Each claim stores subject, predicate, object assertion, optional temporal anchor, source-prior extraction confidence, provenance URL, category, and `unverified_claim` status. Claims are never auto-verified. Matching claims from a different source host reinforce confidence modestly and append provenance; clear polarity conflicts against a high-confidence matching subject/predicate are preserved as two records with `conflicted_evidence` status and a contradiction-log entry. This is a conservative first pass, not general semantic reasoning: it will miss many paraphrases and should not be used as a sole authority for high-stakes decisions.


### Numeric and temporal claim resolution; reasoning retrieval

The contradiction engine now tags clear scalar mismatches (`numeric_discrepancy`) when subject/predicate match and parsed units are compatible. It supports currency symbols, common magnitude suffixes (K/M/B), percentages, and selected measurement/count units; ambiguous or incomparable quantities are deliberately left unclassified. A newer dated assertion that conflicts with an older dated assertion marks the older record `superseded_temporal`, while retaining both records and leaving the newer assertion `unverified_claim`. These rules resolve timestamps conservatively; they do not establish that either claim is true.

`universal_intelligence_engine.learning.query_claims` exposes `query_claims(...)` and `ClaimQueryService` for reasoning integrations. Results include source URLs and explicit warnings for `unverified_claim`, `conflicted_evidence`, and `superseded_temporal`; callers must not treat retrieval as verification.

A bounded, opt-in live smoke test uses the approved public W3C PNG specification page and the same allowlist, robots, redirect, timeout, and payload-limit code path as ingestion:

```powershell
$env:UIE_RUN_LIVE_SMOKE='1'
python -m pytest -q tests/test_live_ingestion_smoke.py -m integration
```

It is skipped during ordinary offline test runs. The test checks that the source is fetched, stored in the corpus, distilled into claim memory, and that provenance and `unverified_claim` status are preserved. External site availability and robots policy can change; a skipped or failing live test must not be reported as a successful harvest.


### Curiosity-driven bounded learning cycle

The learning package now includes `CuriosityAgent`, `AutonomousLearner`, and `HeuristicMemory`. The curiosity agent maps missing or flagged query topics to the reviewed seed catalog, returning structured targets with topic, category/domain, target entity, temporal focus, and seed URL. Each cycle is bounded to at most three target categories, five discovered candidates per target, and a configurable execution deadline capped at 60 seconds. It reuses the existing ingestion pipeline rather than introducing a separate fetcher, so allowlist, robots, redirect validation, timeout, and payload limits remain in force.

`ClaimQueryService.query(..., trigger_learning=True)` opts into a non-blocking background cycle when the requested subject has no claims or carries claim warnings. At most one cycle per learner instance runs at a time. This is query-triggered autonomous work, not a permanent always-on scheduler; durable queue/storage and an external process supervisor are still needed for reliable continuous operation across restarts.

`HeuristicMemory` persists auditable provisional rules. Rules derived from unreviewed public sources are capped at confidence 0.75 and require at least two distinct source hosts, except temporal-revision rules which record a revision-handling heuristic without asserting factual truth. The claim memory's public-web corroboration confidence is likewise capped at 0.75. Heuristics are advisory only and do not alter executable code or security policy.


### Cryptographic provenance and lightweight semantic retrieval

Learned claim and heuristic writes are appended to a SHA-256 hash chain in the shared SQLite memory database. The ContradictionEngine.verify_chain() method checks hash linkage, payload digests, and that current claim/rule rows match the latest chained snapshots. Treat a failed verification or legacy rows without chain coverage as an integrity warning; the chain is tamper-evident, not a substitute for access controls or a separately protected external checkpoint.

Clinical lesson promotion requires the configured reviewer token and stores an HMAC-SHA256 signature over the lesson ID, domain, text, reviewer, and doctor_validated status. Configure UIE_DOCTOR_SIGNING_KEY as a high-entropy secret distinct from application credentials; when absent, the existing UIE_LESSON_REVIEW_TOKEN is used for compatibility. Validated lessons with missing/invalid signatures are not recalled.

Semantic retrieval uses cosine scoring over deterministic 384-dimensional token/character-ngram hashing vectors by default, with a small explicit synonym bridge. NumPy is used if installed; otherwise cosine scoring uses the Python standard library. An external embedding callback can supply 384-dimensional vectors, and float32 vectors can be persisted in the SQLite semantic_vectors table. Hashed vectors are a lexical-similarity fallback, not a pretrained semantic embedding model and can miss domain-specific paraphrases.
