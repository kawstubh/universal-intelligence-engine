from pathlib import Path

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.learning.autonomous_learner import AutonomousLearner
from universal_intelligence_engine.learning.cognitive_memory import CognitiveMemoryEngine
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.epistemic_dispatcher import EpistemicDiscoveryDispatcher
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.learning.query_claims import ClaimQueryService
from universal_intelligence_engine.learning.curiosity_agent import CuriosityAgent
from universal_intelligence_engine.reasoning.deliberation import UIEReasoningCoordinator
from universal_intelligence_engine.reasoning.prm_deliberation import ProcessRewardEvaluator
from universal_intelligence_engine.learning.deep_reflexion import DeepReflexionEngine


ROOT = Path(__file__).parents[1]
SEEDS = ROOT / "src/universal_intelligence_engine/config/seeds.json"


class FakeHarvestPipeline:
    def __init__(self, claim_memory, harvested_url):
        self.claim_memory = claim_memory
        self.seed_path = SEEDS
        self.harvested_url = harvested_url
        self.enqueued = []
        self.batches = []

    def enqueue(self, urls, category=None):
        self.enqueued.extend(urls)
        return {"queued": len(urls), "rejected": 0}

    def run_batch(self, limit=5, urls=None):
        self.batches.append((limit, list(urls or [])))
        chosen = self.harvested_url if self.harvested_url in (urls or []) else (urls or [None])[0]
        return {"processed": 1 if chosen else 0, "results": ([{"url": chosen, "canonical_url": chosen,
            "status": "stored", "claims_processed": 1}] if chosen else [])}


class EchoReasoner:
    def reason(self, request, evidence):
        return IntelligenceResponse(answer="bounded answer", evidence=tuple(evidence), confidence=0.4, metadata={})


def evidence(source, content, url=None):
    return Evidence(source=source, title="W3C protocol relations", content=content,
                    confidence=0.8, retrieved_at="2026-10-15T00:00:00+00:00",
                    url=url, metadata={})


def test_void_detection_dispatches_without_waiting_for_harvest(tmp_path):
    memory = CognitiveMemoryEngine(tmp_path / "void.sqlite3")
    coordinator = UIEReasoningCoordinator(deep_reflexion=DeepReflexionEngine(memory))
    request = IntelligenceRequest(
        goal="Compare unresolved W3C protocol relationships across independent sources",
        context={"focus_entity": "W3C protocol"},
    )
    response = coordinator.reason(request, [
        evidence("unverified-a", "W3C protocol relationship remains unknown"),
        evidence("unverified-b", "No verified protocol relationship was found"),
    ], EchoReasoner())
    dispatch = response.metadata["epistemic_discovery"]
    assert dispatch["enqueued"] is True
    assert dispatch["non_blocking"] is True
    assert dispatch["evidence_grounding"] < 0.60
    assert dispatch["seed_allowlist"]
    assert len(dispatch["seed_allowlist"]) <= 5
    missions = EpistemicDiscoveryDispatcher(memory.db_path, SEEDS).list_missions("queued")
    assert len(missions) == 1
    assert missions[0]["focus_entity"] == "W3C protocol"


def test_closed_loop_harvest_writes_graph_and_resolves_episode(tmp_path):
    db = tmp_path / "closed-loop.sqlite3"
    memory = CognitiveMemoryEngine(db)
    dispatcher = EpistemicDiscoveryDispatcher(db, SEEDS)
    mission = dispatcher.enqueue(focus_entity="W3C protocol", query_text="latest W3C protocol relationship details",
                                 target_domains=["technology_science"])
    assert mission["enqueued"] is True
    harvest_url = mission["seed_allowlist"][0]
    claims = ContradictionEngine(db)
    claims.process_claim(DistilledClaim("W3C protocol", "documents", "a defined public protocol relation",
                                        "2026", 0.70, harvest_url, "technology_science"))
    episode = memory.record_episode(goal="W3C protocol relationship details", domain="technology_science",
                                    feedback={"knowledge_deficit": "missing relation"})
    pipeline = FakeHarvestPipeline(claims, harvest_url)
    learner = AutonomousLearner(pipeline=pipeline, curiosity=CuriosityAgent(ClaimQueryService(claims), seed_path=SEEDS),
                                claim_memory=claims, heuristic_memory=HeuristicMemory(db))
    result = learner._drain_epistemic_missions(max_entities=2)
    assert result["missions_claimed"] == 1
    assert result["graph_relations_added"] >= 1
    assert pipeline.batches and len(pipeline.batches[0][1]) <= 5
    assert dispatcher.list_missions()[0]["status"] == "resolved"
    refreshed = CognitiveMemoryEngine(db).recall_episodes("W3C protocol")
    assert refreshed[0]["id"] == episode["id"]
    assert refreshed[0]["feedback"]["epistemic_resolution"]["mission_id"] == mission["mission_id"]
    score, _, signals = ProcessRewardEvaluator().score_trajectory(
        "W3C protocol relationship details",
        [evidence("verified-source", "W3C protocol documents a defined public protocol relation", harvest_url)])
    assert signals["evidence_grounding"] >= 0.75
    assert score > 0.0
    assert CognitiveMemoryEngine(db).verify_integrity()[0] is True


def test_active_mission_deduplication_and_hard_bounds(tmp_path):
    dispatcher = EpistemicDiscoveryDispatcher(tmp_path / "dedupe.sqlite3", SEEDS)
    first = dispatcher.enqueue(focus_entity="Example entity", query_text="technology protocol gap",
                               target_domains=["technology_science"])
    second = dispatcher.enqueue(focus_entity=" example ENTITY ", query_text="technology protocol gap",
                                target_domains=["technology_science"])
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["reason"] == "duplicate_active_mission"
    assert len(dispatcher.claim_batch(limit=99)) == 1
    assert dispatcher.claim_batch(limit=2) == []
    assert len(first["seed_allowlist"]) <= 5
    assert len(first["target_domains"]) <= 3
