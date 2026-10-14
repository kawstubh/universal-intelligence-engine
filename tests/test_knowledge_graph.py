import sqlite3

from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.contradiction import ContradictionEngine


def test_cross_domain_claims_connect_in_two_hop_traversal(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3", confidence_decay=0.8)
    first = DistilledClaim("W3C PNG specification", "defines", "portable network graphics",
                           None, 0.9, "https://www.w3.org/TR/PNG/", "technology_science")
    second = DistilledClaim("portable network graphics", "is_used_by", "medical imaging systems",
                            None, 0.7, "https://www.who.int/example", "health_life_sciences")
    store.ingest_claim(first)
    store.ingest_claim(second)
    graph = store.get_entity_subgraph("W3C PNG specification", max_hops=2)
    names = {node["normalized_name"] for node in graph["nodes"]}
    assert "w3c png specification" in names
    assert "portable network graphics" in names
    assert "medical imaging systems" in names
    assert max(node["hop"] for node in graph["nodes"]) == 2
    assert any(edge["hop"] == 2 for edge in graph["relations"])
    two_hop = [edge for edge in graph["relations"] if edge["hop"] == 2]
    assert two_hop
    assert all(edge["traversal_confidence"] <= edge["raw_confidence"] for edge in two_hop)


def test_graph_confidence_decay_and_contradiction_logging(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3", confidence_decay=0.5)
    store.ingest_claim({"subject":"Entity A","predicate":"connects_to","object_claim":"Entity B",
                        "confidence":0.8,"provenance_url":"https://a.example/1","domain":"technology_science"})
    store.ingest_claim({"subject":"Entity B","predicate":"connects_to","object_claim":"Entity C",
                        "confidence":0.6,"provenance_url":"https://b.example/1","domain":"health_life_sciences"})
    graph = store.get_entity_subgraph("Entity A", max_hops=2)
    edge_two = next(edge for edge in graph["relations"] if edge["source_name"] == "Entity B")
    assert edge_two["raw_confidence"] == 0.6
    assert edge_two["traversal_confidence"] == 0.3
    store.ingest_claim({"subject":"Entity A","predicate":"connects_to","object_claim":"Entity D",
                        "confidence":0.75,"provenance_url":"https://c.example/1","domain":"technology_science"})
    conflicts = store.get_contradictions()
    assert len(conflicts) == 1
    assert conflicts[0]["detail_tag"] == "graph_edge_conflict"
    graph = store.get_entity_subgraph("Entity A", max_hops=1)
    conflicted = [edge for edge in graph["relations"] if edge["predicate"] == "connects_to"]
    assert sum(edge["status"] == "conflicted_evidence" for edge in conflicted) == 2


def test_graph_store_backfills_existing_claims_compatibly(tmp_path):
    db = tmp_path / "graph.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE claims (
            id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object_claim TEXT, temporal_anchor TEXT,
            confidence REAL, provenance_url TEXT, domain TEXT, status TEXT, provenance_urls TEXT,
            created_at TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("c1","Existing subject","relates_to","Existing object",None,0.6,
                      "https://example.org/claim","general_reference","unverified_claim",'["https://example.org/claim"]',"2026-01-01","2026-01-01"))
    store = KnowledgeGraphStore(db)
    assert store.sync_existing_claims() == {"synced":1}
    assert store.sync_existing_claims() == {"synced":0}
    graph = store.get_entity_subgraph("Existing subject", max_hops=1)
    assert len(graph["relations"]) == 1


def test_claim_engine_projects_new_claims_into_graph(tmp_path):
    db = tmp_path / "integrated.sqlite3"
    claims = ContradictionEngine(db)
    claims.process_claim(DistilledClaim("Source concept", "connects_to", "Target concept",
                                        None, 0.65, "https://example.org/source", "technology_science"))
    graph = KnowledgeGraphStore(db).get_entity_subgraph("Source concept", max_hops=1)
    assert len(graph["relations"]) == 1
    assert graph["relations"][0]["target_name"] == "Target concept"
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.query_claims import ClaimQueryService


def test_claim_query_returns_connected_graph_with_provenance_and_warnings(tmp_path):
    memory = ContradictionEngine(tmp_path / "query-graph.sqlite3")
    memory.process_claim(DistilledClaim("W3C PNG specification", "defines", "portable network graphics",
                                        None, 0.8, "https://www.w3.org/TR/PNG/", "technology_science"))
    memory.process_claim(DistilledClaim("portable network graphics", "used_in", "medical imaging systems",
                                        None, 0.6, "https://www.who.int/example", "health_life_sciences"))

    result = ClaimQueryService(memory=memory).query("W3C PNG specification", max_hops=2)

    graph = result["knowledge_graph"]
    assert graph["root"]["name"] == "W3C PNG specification"
    assert {node["name"] for node in graph["nodes"]} >= {
        "W3C PNG specification", "portable network graphics", "medical imaging systems"
    }
    assert any(edge["provenance_url"] == "https://www.w3.org/TR/PNG/" for edge in graph["relations"])
    assert result["graph_warnings"]
    assert all(item["warning"] for item in result["graph_warnings"])


def test_claim_query_can_disable_graph_for_legacy_consumers(tmp_path):
    memory = ContradictionEngine(tmp_path / "query-graph-optout.sqlite3")
    memory.process_claim(DistilledClaim("A concept", "relates_to", "B concept", None, 0.5,
                                        "https://example.org/a", "general_reference"))
    result = ClaimQueryService(memory=memory).query("A concept", include_graph=False)
    assert "knowledge_graph" not in result
    assert "graph_warnings" not in result
