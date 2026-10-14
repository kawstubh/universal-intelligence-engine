import json
from pathlib import Path

from universal_intelligence_engine.learning.autonomous_learner import AutonomousLearner
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.curiosity_agent import CuriosityAgent
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.learning.query_claims import ClaimQueryService


class FakeStore:
    allowed_domains = {"w3.org", "who.int", "worldbank.org"}


class FakePipeline:
    def __init__(self, memory):
        self.store = FakeStore()
        self.queue_path = ".data/fake-queue.sqlite3"
        self.claim_memory = memory
        self.discover_calls = []
        self.run_calls = []

    def discover(self, max_sources=3, max_urls_per_source=20):
        self.discover_calls.append((max_sources, max_urls_per_source))
        return {"sources_checked": 1, "discovered_candidates": max_urls_per_source, "queued_new": max_urls_per_source}

    def run_batch(self, limit=5):
        self.run_calls.append(limit)
        return {"processed": 1, "results": [{"status": "stored", "url": "https://www.w3.org/TR/PNG/", "claims_extracted": 1}]}


def _curiosity(memory):
    return CuriosityAgent(ClaimQueryService(memory), seed_path=Path(__file__).parents[1] / "src/universal_intelligence_engine/config/seeds.json")


def test_curiosity_generates_allowlisted_bounded_targets_for_gap(tmp_path):
    memory = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    seed_path = Path(__file__).parents[1] / "src/universal_intelligence_engine/config/seeds.json"
    curiosity = CuriosityAgent(ClaimQueryService(memory), seed_path=seed_path)
    targets = curiosity.inspect("latest W3C technology specification unknown", max_targets=99)
    assert 1 <= len(targets) <= 3
    assert all(set(["topic", "domain", "target_entity", "temporal_focus", "seed_url", "target_host"]) <= set(t) for t in targets)
    assert all(t["seed_url"].startswith("https://") for t in targets)
    seed_hosts = {Path(url).name for urls in json.loads(seed_path.read_text(encoding="utf-8-sig")).values() for url in urls}
    from urllib.parse import urlparse
    allowed_hosts = {urlparse(url).hostname for urls in json.loads(seed_path.read_text(encoding="utf-8-sig")).values() for url in urls}
    assert all(t["target_host"] in allowed_hosts for t in targets)
    assert all(t["temporal_focus"] >= 2026 for t in targets)


def test_mock_learning_cycle_consolidates_independent_claim_and_persists_heuristic(tmp_path):
    memory = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    base = DistilledClaim("W3C PNG specification", "is", "a public image specification version 1", "as of 2026", 0.70,
                          "https://www.w3.org/TR/PNG/", "technology_science")
    memory.process_claim(base)
    second = DistilledClaim(base.subject, base.predicate, base.object_claim, base.temporal_anchor, 0.70,
                           "https://docs.python.org/3/library/index.html", "technology_science")
    memory.process_claim(second)
    stored = memory.get_claims("W3C PNG specification")[0]
    assert len(stored["provenance_urls"]) == 2

    fake = FakePipeline(memory)
    learner = AutonomousLearner(fake, _curiosity(memory), memory, HeuristicMemory(str(tmp_path / "claims.sqlite3")), timeout_seconds=60)
    result = learner.run_cycle("latest W3C technology specification")
    assert result["domains_processed"] <= 3
    assert fake.discover_calls and all(sources == 1 and urls <= 5 for sources, urls in fake.discover_calls)
    assert fake.run_calls and all(1 <= n <= 5 for n in fake.run_calls)
    rules = learner.heuristics.get_rules()
    assert rules and rules[0]["confidence"] <= 0.75
    assert len(rules[0]["provenance_urls"]) >= 2


def test_safety_bounds_and_query_hook_nonblocking_trigger(tmp_path):
    memory = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    fake = FakePipeline(memory)
    learner = AutonomousLearner(fake, _curiosity(memory), memory, HeuristicMemory(str(tmp_path / "claims.sqlite3")), timeout_seconds=60)
    result = learner.run_cycle("latest climate technology specification government policy")
    assert result["domains_processed"] <= 3
    assert len(fake.discover_calls) <= 3
    assert all(urls <= 5 for _, urls in fake.discover_calls)
    assert sum(fake.run_calls) <= 15
    assert result["elapsed_seconds"] < 60

    class StubLearner:
        def __init__(self): self.queries = []
        def trigger_background(self, query):
            self.queries.append(query)
            return {"scheduled": True, "status": "started"}
    stub = StubLearner()
    response = ClaimQueryService(memory, learner=stub).query("unmapped research topic", trigger_learning=True)
    assert response["learning_trigger"]["scheduled"] is True
    assert stub.queries == ["unmapped research topic"]
