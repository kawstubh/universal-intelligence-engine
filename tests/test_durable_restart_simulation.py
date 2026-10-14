import gc
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.learning.scheduler import CuriosityScheduler
from universal_intelligence_engine.security.provenance_crypto import clinical_override_payload, sign_clinical_override, verify_clinical_override


class DummyLearner:
    def run_cycle(self, query):
        return {"domains_processed": 1, "queued_urls": 1}


def test_real_file_restart_preserves_graph_hash_chain_temporal_status_and_heuristics(tmp_path):
    db = tmp_path / "restart.sqlite3"
    graph = KnowledgeGraphStore(db)
    old = graph.ingest_claim({"subject":"Policy R", "predicate":"status", "object_claim":"draft", "confidence":.6,
                              "provenance_url":"https://source.example/old", "domain":"government"}, claim_id="old")
    graph.ingest_claim({"subject":"Policy R", "predicate":"status", "object_claim":"final", "confidence":.6,
                        "provenance_url":"https://source.example/new", "domain":"government"}, claim_id="new")
    graph.resolve_temporal_successor(subject="Policy R", predicate="status", older_object="draft", newer_object="final", older_claim_id="old")
    heuristics = HeuristicMemory(db)
    heuristics.store_constraint(domain="government", subject="Policy R", predicate="status", rule_text="Do not assert draft as current.", provenance_urls=["https://source.example/old"], rule_type="temporal_revision")
    root_before = graph._connect().execute("SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1").fetchone()[0]
    assert graph.verify_integrity_chain("uie_relations")[0]
    del graph, heuristics
    gc.collect()
    graph2 = KnowledgeGraphStore(db)
    heuristics2 = HeuristicMemory(db)
    scheduler = CuriosityScheduler(DummyLearner(), state_path=str(db))
    result = graph2.get_entity_subgraph("Policy R", max_hops=1)
    old_edge = next(edge for edge in result["relations"] if edge["claim_id"] == "old")
    root_after = graph2._connect().execute("SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1").fetchone()[0]
    assert root_after == root_before
    assert old_edge["status"] == "superseded_temporal"
    assert heuristics2.get_rules()[0]["rule_text"] == "Do not assert draft as current."
    assert graph2.verify_integrity_chain("uie_relations")[0]
    assert scheduler.health()["metrics"]["cycles"] == 0


def test_graph_edge_tampering_is_detected(tmp_path):
    db = tmp_path / "tamper.sqlite3"
    graph = KnowledgeGraphStore(db)
    graph.ingest_claim({"subject":"A","predicate":"knows","object_claim":"B","confidence":.5,"provenance_url":"https://example.org","domain":"general_reference"})
    with graph._connect() as conn:
        conn.execute("UPDATE uie_relations SET confidence=0.99")
    ok, error = graph.verify_integrity_chain("uie_relations")
    assert not ok
    assert error


def test_doctor_validated_status_requires_valid_hmac():
    payload = clinical_override_payload("lesson-1", "clinical", "Reviewed guidance", "doctor-7")
    signature = sign_clinical_override(payload, "test-secret-key")
    assert verify_clinical_override(payload, signature, "test-secret-key")
    assert not verify_clinical_override(payload, signature, "wrong-key")
    changed = {**payload, "lesson_text": "Tampered guidance"}
    assert not verify_clinical_override(changed, signature, "test-secret-key")


def test_integrity_ledger_is_linear_and_payload_tampering_has_specific_error(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    graph = KnowledgeGraphStore(db)
    graph.ingest_claim({"subject":"Policy Z", "predicate":"status", "object_claim":"draft", "confidence":.6,
                        "provenance_url":"https://source.example/old", "domain":"government"}, claim_id="old")
    graph.ingest_claim({"subject":"Policy Z", "predicate":"status", "object_claim":"final", "confidence":.6,
                        "provenance_url":"https://source.example/new", "domain":"government"}, claim_id="new")
    graph.resolve_temporal_successor(subject="Policy Z", predicate="status", older_object="draft", newer_object="final", older_claim_id="old")
    HeuristicMemory(db).store_constraint(domain="government", subject="Policy Z", predicate="status",
        rule_text="Prefer the final dated version.", provenance_urls=["https://source.example/old"], rule_type="temporal_revision")
    with graph._connect() as conn:
        rows = conn.execute("SELECT sequence_id,prev_hash,record_hash,event_type FROM uie_integrity_ledger ORDER BY sequence_id").fetchall()
        assert [r["sequence_id"] for r in rows] == list(range(1, len(rows) + 1))
        assert rows[0]["prev_hash"] == "0" * 64
        assert all(rows[i]["prev_hash"] == rows[i-1]["record_hash"] for i in range(1, len(rows)))
        assert any(r["event_type"] == "SUPERSEDED_TEMPORAL" for r in rows)
        conn.execute("UPDATE uie_integrity_ledger SET payload_canonical_json='{}' WHERE sequence_id=1")
    ok, error = graph.verify_integrity_chain("uie_integrity_ledger")
    assert not ok
    assert error and "record_hash mismatch" in error
