# UIE Runtime Verification Milestones 1–4

All examples are deterministic synthetic demonstrations. Passing tests validate software invariants, not clinical/scientific truth or permission to actuate physical systems.

| Milestone | Implemented verification coverage | Boundaries |
|---|---|---|
| 1 — Runtime hardening | Bounded symbolic arithmetic/unit checks; adversarial instruction sanitization with audit event; PRM numeric contradiction penalties. | Arithmetic verifies explicit expressions only; sanitization reduces known injection patterns but is not a proof of safety. |
| 2 — Causal reasoning and MCP | Directed causal graph projections; bounded JSON-RPC MCP lifecycle; endpoint, timeout, payload, redirect, and ledger protections; PRM risk penalties. | Causal projections are hypotheses, not validated effects; MCP tools do not authorize consequential actions. |
| 3 — Multimodal and offline mesh | Structured spatial bounding-box and scalar telemetry ingestion; spatial evidence grounding; Ed25519-signed offline event batches; registered keys, hash-chain checks, atomic/idempotent reconciliation. | Structured feature ingestion only (not a vision model); signatures establish provenance/continuity, not sensor truth. Register keys via a trusted administrative channel. |
| 4 — Adaptive closed loop and testbed | Runtime premise registration/invalidation; trajectory re-score callback; bounded compute-budget escalation for rapid spatial change/high causal risk/adversarial alerts; immutable control adjustment ledger events; multi-scenario local demo; cross-modality regression tests. | Controller re-scores only and sets `actuation_permitted=false`; it does not drive physical actuators. Compute budget is a logical allowance, not a real-time guarantee. |

## Local verification

- `python -m compileall -q src scripts`
- `git diff --check`
- `python -m pytest -v`
- `python scripts/demo_uie_live.py --no-pause`

The live demo uses temporary local SQLite storage, synthetic fixtures, and no external network calls. The demo covers symbolic arithmetic and a graph-derived contraindication fixture, spatial relation ingestion and premise invalidation, adversarial payload sanitization, Ed25519 offline sync, and ledger continuity.

## Operational boundary

PR #28 is review-only until explicitly authorized for merge. No production deployment is performed by milestone verification. Any future physical actuation requires a separate authorization/safety layer, independent validation, and fail-safe behavior.
