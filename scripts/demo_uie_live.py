#!/usr/bin/env python3
"""
UIE End-to-End Live Learning & Deliberation Demo.

Runs a local, synthetic verification walkthrough across query processing,
tripartite memory, PRM trajectory deliberation, epistemic discovery dispatch,
and cryptographic provenance verification. It does not fetch network content.
"""
from __future__ import annotations

import argparse
import gc
import sys
import tempfile
from pathlib import Path
from typing import Sequence

# Support direct execution from a repository checkout.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.learning.cognitive_memory import CognitiveMemoryEngine
from universal_intelligence_engine.learning.epistemic_dispatcher import EpistemicDiscoveryDispatcher
from universal_intelligence_engine.reasoning.deliberation import DeliberationController, ProcessRewardEvaluator
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger


class _DemoReasoner:
    """A deterministic local provider; the sample entity is explicitly fictional."""

    def reason(self, request: IntelligenceRequest, evidence: Sequence[Evidence]) -> IntelligenceResponse:
        return IntelligenceResponse(
            answer=(
                "Demo-only result: UIE-701 is a fictional placeholder. The supplied "
                "synthetic evidence is insufficient to establish any clinical indication "
                "or contraindication; consult no part of this output as medical advice."
            ),
            evidence=tuple(evidence),
            confidence=0.25,
            metadata={"provider": "local_demo_stub", "network_access": False},
        )


def _line(label: str, value: object) -> None:
    print(f"      {label:<31} {value}")


def run_demo(db_path: str | None = None, *, pause: bool = True) -> int:
    print("=" * 70)
    print("       UNIVERSAL INTELLIGENCE ENGINE (UIE) ? LIVE COGNITIVE DEMO")
    print("=" * 70)
    print("Local verification only: synthetic evidence, no network requests.")
    print("UIE-701 is fictional; this demonstration provides no medical guidance.")

    temporary = None
    if db_path:
        database = Path(db_path).expanduser().resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="uie-demo-", ignore_cleanup_errors=True)
        database = Path(temporary.name) / "uie_demo.sqlite3"

    try:
        if pause and sys.stdin.isatty():
            input("\nPress Enter to run the five-layer walkthrough...")
        print("\n[1/5] Query processing & tripartite memory bootstrap")
        memory = CognitiveMemoryEngine(database)
        memory.graph.ingest_claim({
            "subject": "UIE demo memory",
            "predicate": "contains_demo_fixture",
            "object_claim": "synthetic placeholder only",
            "domain": "general_reference",
            "provenance_url": "https://example.org/uie-demo-fixture",
            "confidence": 0.5,
            "status": "unverified_claim",
        })
        memory.synthesize_constraint(
            domain="general_reference",
            subject="demo-fixture",
            predicate="safety_boundary",
            rule_text="Treat UIE-701 as fictional demo data; do not infer clinical facts.",
            provenance_urls=["https://example.org/uie-demo-fixture"],
            confidence=0.65,
        )
        memory.record_episode(
            goal="UIE demo bootstrap",
            domain="general_reference",
            trace={"source": "demo", "network_access": False},
            feedback={"fixture_only": True},
        )
        _line("Episodic store", "uie_episodes initialized")
        _line("Semantic graph", "uie_entities / uie_relations initialized")
        _line("Procedural memory", "heuristic_rules initialized")
        _line("Prior episode recall", len(memory.recall_episodes("UIE demo bootstrap")))

        query = (
            "Compare hypothetical clinical indications and potential contraindications "
            "of fictional compound UIE-701 across evidence sources, and identify "
            "uncertainty and missing relations."
        )
        request = IntelligenceRequest(
            goal=query,
            context={
                "domain": "health_life_sciences",
                "focus_entity": "compound UIE-701",
                "knowledge_graph_void": True,
                "demo_mode": True,
            },
            constraints={"no_clinical_advice": True, "synthetic_evidence_only": True},
        )
        print("\n[2/5] PRM trajectory deliberation")
        print(f'      Query: "{query}"')
        evidence = [
            Evidence(
                source="synthetic_fixture_A",
                title="Fictional UIE-701 record ? unverified fixture",
                retrieved_at="2026-10-15T00:00:00+00:00",
                content="Synthetic placeholder record; no verified indications or contraindications are supplied.",
                confidence=0.35,
                metadata={"domain": "health_life_sciences", "synthetic": True},
            ),
            Evidence(
                source="synthetic_fixture_B",
                title="Fictional UIE-701 evidence gap ? unverified fixture",
                retrieved_at="2026-10-15T00:00:00+00:00",
                content="The hypothetical compound has no verified evidence relations in this demo fixture.",
                confidence=0.30,
                metadata={"domain": "health_life_sciences", "synthetic": True},
            ),
        ]
        controller = DeliberationController(ProcessRewardEvaluator())
        search = controller.search(request, evidence)
        trajectories = search.get("trajectories", [])
        selected = next(
            (item for item in trajectories if item.get("trajectory_id") == search.get("selected_trajectory_id")),
            {},
        )
        signals = selected.get("signals", {})
        grounding = float(signals.get("evidence_grounding", 0.0) or 0.0)
        _line("Reasoning mode", "bounded PRM" if search.get("enabled") else "direct")
        _line("Trajectories evaluated", search.get("trajectory_count", 0))
        _line("Logical consistency", f"{float(signals.get('logical_consistency', 0.0)):.2f}")
        _line("Evidence grounding", f"{grounding:.2f}")
        _line("Domain constraint adherence", f"{float(signals.get('domain_constraint_adherence', 0.0)):.2f}")
        _line("Winning trajectory", search.get("selected_trajectory_id", "none"))
        response = _DemoReasoner().reason(request, search.get("selected_evidence", evidence))
        _line("Local response provider", response.metadata.get("provider"))
        print("      Response:", response.answer)

        print("\n[3/5] Tripartite recall after query")
        episodes = memory.recall_episodes("UIE demo bootstrap", limit=3)
        subgraph = memory.graph.get_entity_subgraph("UIE demo memory", max_hops=1)
        rules = memory.heuristics.get_rules("general_reference")
        _line("Matching episodic records", len(episodes))
        _line("Semantic graph relations", len(subgraph.get("relations", [])))
        _line("Procedural rules recalled", len(rules))

        print("\n[4/5] Epistemic void detection & bounded dispatch")
        dispatcher = EpistemicDiscoveryDispatcher(
            db_path=database,
            seed_path=REPO_ROOT / "src" / "universal_intelligence_engine" / "config" / "seeds.json",
        )
        void_detected = grounding < 0.60 or bool(request.context.get("knowledge_graph_void"))
        _line("Void detected", void_detected)
        mission = {"enqueued": False, "reason": "grounding adequate"}
        if void_detected:
            mission = dispatcher.enqueue(
                focus_entity="compound UIE-701",
                query_text=query,
                target_domains=["health_life_sciences"],
                reason="demo_low_grounding_or_graph_void",
            )
        _line("Mission enqueued", mission.get("enqueued", False))
        _line("Mission ID", mission.get("mission_id", mission.get("reason", "n/a")))
        _line("Approved candidate URLs", len(mission.get("seed_allowlist", [])))
        _line("Candidate URL limit", "5 maximum; no crawl executed")
        memory.record_episode(
            goal=query,
            domain="health_life_sciences",
            trace={"trajectory_count": search.get("trajectory_count", 0),
                   "selected_trajectory_id": search.get("selected_trajectory_id")},
            trajectory_scores=[{"trajectory_id": item.get("trajectory_id"),
                                "score": item.get("score"), "signals": item.get("signals")}
                               for item in trajectories],
            chosen_path_index=search.get("selected_trajectory_id"),
            feedback={"evidence_grounding": grounding, "epistemic_mission_id": mission.get("mission_id"),
                      "demo_mode": True},
        )

        print("\n[5/5] Cryptographic provenance verification")
        valid, error = memory.verify_integrity()
        _line("SHA-256 ledger continuity", "VERIFIED" if valid else "FAILED")
        _line("Ledger error", error or "none")
        conn = memory._connect()
        try:
            ledger_valid, ledger_error = verify_integrity_ledger(conn)
            ledger_events = conn.execute("SELECT COUNT(*) FROM uie_integrity_ledger").fetchone()[0]
        finally:
            conn.close()
        _line("Independent ledger check", "VERIFIED" if ledger_valid else "FAILED")
        _line("Independent ledger error", ledger_error or "none")
        _line("Ledger events", ledger_events)
        if not (valid and ledger_valid):
            print("\nDEMO FAILED: cryptographic integrity verification did not pass.")
            return 1
        if not mission.get("enqueued", False) and void_detected:
            print("\nDEMO WARNING: void was detected, but the approved seed catalog did not allow a mission.")
            return 2
        print("\n" + "=" * 70)
        print("DEMO COMPLETE: all five layers executed; integrity checks passed.")
        print("No external URLs were fetched. The queued mission was not harvested.")
        print("=" * 70)
        if db_path:
            print(f"Demo database retained at: {database}")
        return 0
    finally:
        if temporary is not None:
            # SQLite connection context managers commit/rollback but do not close;
            # collect short-lived wrapper connections before deleting the temp DB.
            gc.collect()
            temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run UIE's local cognitive-stack verification demo.")
    parser.add_argument(
        "--db",
        dest="db_path",
        help="Optional SQLite path to retain demo records. Defaults to an isolated temporary database.",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Run without the interactive Enter prompt (useful for CI and automation).",
    )
    args = parser.parse_args()
    return run_demo(args.db_path, pause=not args.no_pause)


if __name__ == "__main__":
    raise SystemExit(main())
