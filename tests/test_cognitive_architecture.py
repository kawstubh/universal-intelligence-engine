import json
import sqlite3

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.learning.cognitive_memory import CognitiveMemoryEngine
from universal_intelligence_engine.learning.deep_reflexion import DeepReflexionEngine
from universal_intelligence_engine.reasoning.prm_deliberation import DeliberationController, ProcessRewardEvaluator
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger


def ev(source, title, content, confidence=0.9, url=None, metadata=None):
    return Evidence(source=source, title=title, content=content, confidence=confidence,
                    retrieved_at="2026-10-15T00:00:00+00:00", url=url, metadata=metadata or {})


def test_prm_prefers_grounded_noncontradictory_trajectory():
    evaluator = ProcessRewardEvaluator()
    grounded = [ev("verified", "Mars orbit", "Mars orbits the Sun every 687 Earth days.", .92, "https://example.org/mars"),
                ev("reference", "Mars year", "A Mars year is approximately 687 Earth days.", .91, "https://example.net/mars-year")]
    contradictory = [ev("unknown", "Mars orbit", "Mars orbits the Sun every 20 days.", .99),
                     ev("unknown", "Mars year", "Mars does not orbit the Sun.", .99)]
    good_score, good_steps, good_signals = evaluator.score_trajectory("Mars orbital period", grounded)
    bad_score, bad_steps, bad_signals = evaluator.score_trajectory("Mars orbital period", contradictory)
    assert good_signals["verified"] is True
    assert good_score > bad_score
    assert all({"logical_consistency", "evidence_grounding", "domain_constraint_adherence"} <= step.keys() for step in good_steps)


def test_tripartite_episode_reflexion_restart_and_ledger_integrity(tmp_path):
    db = tmp_path / "cognitive.sqlite3"
    memory = CognitiveMemoryEngine(db)
    episode = memory.record_episode(goal="Europe PMC query syntax", domain="europe-pmc",
        trace={"failed": True, "reason": "invalid query syntax"}, trajectory_scores=[{"score": .2}],
        chosen_path_index=1, feedback={"critic": "query formatting error"})
    reflection = DeepReflexionEngine(memory).reflect(goal="Europe PMC query syntax", failure="invalid query syntax", domain="europe-pmc")
    assert reflection["stored"]
    assert memory.verify_integrity()[0] is True
    restarted = CognitiveMemoryEngine(db)
    episodes = restarted.recall_episodes("Europe PMC")
    assert episodes[0]["id"] == episode["id"]
    assert episodes[0]["trajectory_scores"] == [{"score": .2}]
    assert any(rule["predicate"] == "query_or_parameter_error" for rule in restarted.heuristics.get_rules("europe-pmc"))
    assert restarted.verify_integrity()[0] is True


def test_dynamic_budget_direct_lookup_vs_conflicting_edges():
    controller = DeliberationController()
    direct = controller.budget(IntelligenceRequest(goal="capital of France"), [ev("encyclopedia", "Capital", "Paris is the capital of France.")])
    conflict = [ev("source-a", "Population", "The city population is 100000.", .9),
                ev("source-b", "Population", "The city population is 200000.", .9)]
    graph_conflict = controller.budget(IntelligenceRequest(goal="city population", context={"graph_conflicts": 2}), conflict[:1])
    multi = controller.budget(IntelligenceRequest(goal="compare conflicting population estimates across sources"), conflict)
    assert direct.enabled is False and direct.reason == "direct_lookup"
    assert multi.enabled is True and multi.beam_width > direct.beam_width
    assert graph_conflict.enabled is True and graph_conflict.reason == "conflict_or_multi_step_query"
