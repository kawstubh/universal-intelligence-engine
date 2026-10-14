from __future__ import annotations

import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from universal_intelligence_engine.learning.causal_engine import CausalWorldModel
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.perception.multimodal_ingest import MultimodalIngestor
from universal_intelligence_engine.reasoning.adaptive_controller import AdaptiveEnvironmentController
from universal_intelligence_engine.reasoning.symbolic_verifier import verify_arithmetic
from universal_intelligence_engine.security.mesh_sync import create_signed_sync_bundle, reconcile_offline_batch, register_node_public_key
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger


def test_dynamic_premise_invalidation_rescores_trajectory(tmp_path):
    db = tmp_path / "adaptive.sqlite3"
    rescored = []
    controller = AdaptiveEnvironmentController(db, rescore=lambda plan: rescored.append(dict(plan)))
    controller.register_active_premises({"temperature": 20, "door_open": False})
    result = controller.process_event({"event_type": "sensor_telemetry", "event_id": "telemetry-1",
                                       "observations": {"temperature": 35}})
    assert result.premise_invalidated is True
    assert result.trajectory_rescored is True
    assert rescored[0]["invalidated_premises"] == ["temperature"]
    assert result.ledger_sequence == 1
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert verify_integrity_ledger(conn) == (True, None)


def test_cross_modality_ledger_continuity(tmp_path):
    db = tmp_path / "modalities.sqlite3"
    graph = KnowledgeGraphStore(db)
    spatial = MultimodalIngestor(graph).ingest_frame({"source_id": "field", "frame_type": "spatial_grid",
        "entities": [{"id": "area", "label": "area", "bounding_box": [0, 0, 1, 1]},
                      {"id": "object", "label": "object", "bounding_box": [0.1, 0.1, 0.2, 0.2]}]})
    assert spatial.relations_created > 0
    graph.ingest_claim({"subject": "agent", "predicate": "CAUSES", "object_claim": "risk", "confidence": 0.8,
                        "domain": "test", "provenance_url": "uie-test://causal", "status": "unverified_claim"})
    causal = CausalWorldModel(graph).simulate_intervention("agent", "act", max_depth=2)
    assert causal.traversed_edges > 0
    assert verify_arithmetic("12 / 3", 4).valid
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    register_node_public_key(db, "node-1", public)
    bundle = create_signed_sync_bundle("node-1", "batch-1", [{"event_id": "event-1", "event_type": "test", "payload": {"ok": True}}], private)
    result = reconcile_offline_batch(bundle, db)
    assert result.accepted and result.ledger_verified
    controller = AdaptiveEnvironmentController(db)
    controller.process_event({"event_type": "sensor_telemetry", "observations": {"status": "verified"}})
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        valid, error = verify_integrity_ledger(conn)
        assert valid, error
        total = conn.execute("SELECT COUNT(*) FROM uie_integrity_ledger").fetchone()[0]
        assert total >= 2


def test_controller_escalates_budget_for_spatial_and_causal_risk(tmp_path):
    controller = AdaptiveEnvironmentController(tmp_path / "risk.sqlite3", base_compute_budget=4, max_compute_budget=16)
    result = controller.process_event({"event_type": "spatial_shift", "spatial_delta": 0.8, "causal_risk": 0.9})
    assert result.trajectory_rescored
    assert result.compute_budget == 16
    assert "rapid_spatial_change" in result.reasons
    assert "high_causal_risk" in result.reasons
