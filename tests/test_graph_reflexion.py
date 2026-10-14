from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.learning.query_claims import ClaimQueryService


class FakeLearner:
    def __init__(self):
        self.calls = []

    def trigger_background(self, query):
        self.calls.append(query)
        return {"scheduled": True, "status": "started", "query": query}


def test_conflicted_graph_dispatches_bounded_discovery_without_blocking_query(tmp_path):
    db = tmp_path / "reflexion-conflict.sqlite3"
    memory = ContradictionEngine(db)
    memory.process_claim(DistilledClaim("Entity A", "has_relation", "Entity B", None, 0.7,
                                        "https://source-one.example/a", "technology_science"))
    memory.process_claim(DistilledClaim("Entity A", "has_relation", "Entity C", None, 0.7,
                                        "https://source-two.example/a", "technology_science"))
    learner = FakeLearner()
    response = ClaimQueryService(memory=memory, learner=learner).query("Entity A")
    reflexion = response["graph_reflexion"]
    assert any(issue["type"] == "conflicted_edge" for issue in reflexion["issues"])
    assert reflexion["discovery"]["scheduled"] is True
    assert len(learner.calls) == 1
    assert "Entity A" in learner.calls[0]
    assert response["count"] >= 1


def test_constraint_persists_and_is_recalled_on_identical_query(tmp_path):
    db = tmp_path / "reflexion-recall.sqlite3"
    memory = ContradictionEngine(db)
    memory.process_claim(DistilledClaim("Entity A", "has_relation", "Entity B", None, 0.7,
                                        "https://source-one.example/a", "technology_science"))
    memory.process_claim(DistilledClaim("Entity A", "has_relation", "Entity C", None, 0.7,
                                        "https://source-two.example/a", "technology_science"))
    learner = FakeLearner()
    service = ClaimQueryService(memory=memory, learner=learner)
    first = service.query("Entity A")
    second = service.query("Entity A")
    rules = HeuristicMemory(db).get_rules()
    constraints = [rule for rule in rules if rule["rule_type"] == "conflicted_edge"]
    assert constraints
    assert any("do not assert" in rule["rule_text"] for rule in constraints)
    assert second["graph_reflexion"]["recalled_constraints"]
    assert all(rule["confidence"] <= 0.75 for rule in constraints)


def test_corroboration_boosts_edge_asymptotically_and_caps_confidence(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "edge-boost.sqlite3")
    first = store.ingest_claim({"subject": "Entity X", "predicate": "related_to", "object_claim": "Entity Y",
                                "confidence": 0.7, "provenance_url": "https://one.example/x", "domain": "technology_science"})
    store.ingest_claim({"subject": "Entity X", "predicate": "related_to", "object_claim": "Entity Y",
                        "confidence": 0.99, "provenance_url": "https://two.example/x", "domain": "technology_science"})
    graph = store.get_entity_subgraph("Entity X", max_hops=1)
    edges = [edge for edge in graph["relations"] if edge["target_name"] == "Entity Y"]
    assert edges[0]["confidence"] > first["confidence"]
    assert all(edge["confidence"] <= 0.75 for edge in edges)
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.query_claims import ClaimQueryService


def test_stale_temporal_anchor_creates_caution_and_dispatches_discovery(tmp_path):
    db = tmp_path / "reflexion-temporal.sqlite3"
    memory = ContradictionEngine(db)
    memory.process_claim(DistilledClaim("Policy X", "has_status", "active", "as of 2020", 0.7,
                                        "https://archive.example/policy", "politics_government_countries"))
    learner = FakeLearner()
    result = ClaimQueryService(memory=memory, learner=learner).query("Policy X")
    reflexion = result["graph_reflexion"]
    assert any(issue["type"] == "stale_temporal_anchor" for issue in reflexion["issues"])
    assert any("as current" in rule["rule_text"] for rule in HeuristicMemory(db).get_rules())
    assert reflexion["discovery"]["scheduled"] is True
