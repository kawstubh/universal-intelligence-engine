from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.temporal_resolver import TemporalResolver, parse_temporal


def test_explicit_revision_supersedes_prior_edge_and_preserves_history(tmp_path):
    graph = KnowledgeGraphStore(tmp_path / "temporal-graph.sqlite3")
    older = {"id": "claim-old", "subject": "Standard Q", "predicate": "status", "object_claim": "version 1", "temporal_anchor": "2024-01-10"}
    newer = {"id": "claim-new", "subject": "Standard Q", "predicate": "status", "object_claim": "version 2", "temporal_anchor": "2025-03-01"}
    old_edge = graph.ingest_claim({**older, "confidence": 0.7, "provenance_url": "https://standards.example/v1", "domain": "technology_science"}, claim_id="claim-old")
    graph.ingest_claim({**newer, "confidence": 0.7, "provenance_url": "https://standards.example/v2", "domain": "technology_science"}, claim_id="claim-new")
    resolved = TemporalResolver(graph).resolve(older, newer, newer_metadata="Revision 2 supersedes version 1")
    assert resolved["resolved"] is True
    assert resolved["status"] == "superseded_temporal"
    edges = graph.get_entity_subgraph("Standard Q", max_hops=1)["relations"]
    prior = next(edge for edge in edges if edge["id"] == old_edge["id"])
    assert prior["status"] == "superseded_temporal"
    assert prior["provenance_url"] == "https://standards.example/v1"
    assert len(edges) == 2


def test_later_date_without_revision_evidence_is_not_auto_superseded(tmp_path):
    graph = KnowledgeGraphStore(tmp_path / "temporal-dispute.sqlite3")
    older = {"id": "old", "subject": "Deal Z", "predicate": "status", "object_claim": "approved", "temporal_anchor": "2024"}
    newer = {"id": "new", "subject": "Deal Z", "predicate": "status", "object_claim": "blocked", "temporal_anchor": "2025"}
    graph.ingest_claim({**older, "confidence": .6, "provenance_url": "https://one.example/a"}, claim_id="old")
    result = TemporalResolver(graph).resolve(older, newer, newer_metadata="Latest report")
    assert result["resolved"] is False
    assert result["reason"] == "later_date_without_revision_evidence"
    edge = next(e for e in graph.get_entity_subgraph("Deal Z", max_hops=1)["relations"] if e["claim_id"] == "old")
    assert edge["status"] == "unverified_claim"


def test_parse_temporal_iso_and_year():
    assert parse_temporal("published 2026-10-14") ["date"].isoformat() == "2026-10-14"
    assert parse_temporal("effective in 2022")["date"].year == 2022
