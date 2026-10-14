"""Spatial feature ingestion and signed disconnected mesh reconciliation tests."""
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger
from universal_intelligence_engine.security.mesh_sync import (
    MeshSecurityViolation, create_signed_sync_bundle, reconcile_offline_batch, register_node_public_key,
)
from universal_intelligence_engine.perception.multimodal_ingest import MultimodalIngestor
from universal_intelligence_engine.contracts import Evidence
from universal_intelligence_engine.reasoning.deliberation import ProcessRewardEvaluator


def _relations(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT r.predicate,s.name,t.name FROM uie_relations r "
            "JOIN uie_entities s ON s.id=r.source_entity_id "
            "JOIN uie_entities t ON t.id=r.target_entity_id"
        ).fetchall()


def test_spatial_grid_projects_adjacency_and_containment(tmp_path):
    path = tmp_path / "spatial.sqlite3"
    store = KnowledgeGraphStore(path)
    ingestor = MultimodalIngestor(store, adjacency_distance=0.2, proximity_distance=0.5)
    result = ingestor.ingest_frame({
        "source_id": "cam-7", "frame_type": "spatial_grid",
        "entities": [
            {"id": "box", "label": "Container", "bounding_box": [0.0, 0.0, 1.0, 1.0], "confidence": 0.95},
            {"id": "inside", "label": "Package", "bounding_box": [0.2, 0.2, 0.4, 0.4], "confidence": 0.9},
            {"id": "near", "label": "Marker", "bounding_box": [0.2, 0.4, 0.4, 0.6], "confidence": 0.8},
            {"id": "bad", "label": "Invalid", "bounding_box": [4, 3, 2, 5], "confidence": 0.9},
        ],
        "telemetry": {"frame_counter": 17, "binary_raster": b"discard-me"},
    })
    rows = _relations(path)
    predicates = [row[0] for row in rows]
    assert "CONTAINS" in predicates
    assert "ADJACENT_TO" in predicates
    assert result.entities_ingested == 3
    assert result.relations_created >= 2
    assert result.rejected_items
    assert result.raw_payload_persisted is False
    assert not any(isinstance(item, bytes) for row in rows for item in row)


def test_sensor_telemetry_scalar_projection_and_binary_discard(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    store = KnowledgeGraphStore(path)
    result = MultimodalIngestor(store).ingest_frame({
        "source_id": "sensor-2", "frame_type": "sensor_telemetry", "entities": [],
        "telemetry": {"temperature_c": 22.5, "pressure_kpa": 101.3, "raw_frame": b"binary"},
    })
    rows = _relations(path)
    assert any(row[0] == "MEASURED_temperature_c" for row in rows)
    assert any(row[0] == "MEASURED_pressure_kpa" for row in rows)
    assert result.rejected_items
    assert result.raw_payload_persisted is False


def test_prm_uses_valid_spatial_provenance_for_grounding():
    evidence = Evidence(source="sensor", title="Spatial observation", retrieved_at="2026-10-15",
                        content="Object A is next to object B", confidence=0.8,
                        metadata={"spatial_assertions": [{"subject": "A", "predicate": "ADJACENT_TO",
                                   "object": "B", "confidence": 0.9, "provenance": "uie-spatial://sensor-1"}]})
    _, signals = ProcessRewardEvaluator().score_step("spatial observation", evidence, set())
    assert signals["spatial_assertions_valid"] is True
    assert signals["evidence_grounding"] == 0.65


def _registered_node(db_path):
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    register_node_public_key(db_path, "edge-node-1", public_pem)
    return private


def test_offline_batch_reanchors_three_events_and_is_idempotent(tmp_path):
    db_path = tmp_path / "master.sqlite3"
    private = _registered_node(db_path)
    bundle = create_signed_sync_bundle("edge-node-1", "batch-001", [
        {"event_id": "evt-1", "event_type": "sensor_reading", "payload": {"value": 12}},
        {"event_id": "evt-2", "event_type": "observation", "payload": {"label": "object"}},
        {"event_id": "evt-3", "event_type": "operator_note", "payload": {"note": "offline"}},
    ], private)
    result = reconcile_offline_batch(bundle, db_path)
    assert result.accepted and result.appended_events == 3 and result.skipped_duplicates == 0
    assert result.ledger_verified
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT COUNT(*) FROM uie_integrity_ledger").fetchone()[0] == 3
        assert verify_integrity_ledger(conn)[0]
    repeated = reconcile_offline_batch(bundle, db_path)
    assert repeated.appended_events == 0 and repeated.skipped_duplicates == 3
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM uie_integrity_ledger").fetchone()[0] == 3


def test_corrupted_signature_or_event_is_rejected_atomically(tmp_path):
    db_path = tmp_path / "master.sqlite3"
    private = _registered_node(db_path)
    bundle = create_signed_sync_bundle("edge-node-1", "batch-bad", [
        {"event_id": "evt-1", "event_type": "observation", "payload": {"value": 1}},
    ], private)
    tampered = {**bundle, "events": [dict(bundle["events"][0], payload={"value": 999})]}
    with pytest.raises(MeshSecurityViolation, match="signature"):
        reconcile_offline_batch(tampered, db_path)
    # Re-signing the altered batch still fails its internal event hash.
    resigned = create_signed_sync_bundle("edge-node-1", "batch-bad-2", [
        {"event_id": "evt-1", "event_type": "observation", "payload": {"value": 1}},
    ], private)
    resigned["events"][0]["payload"]["value"] = 2
    signed_content = {k: resigned[k] for k in ("node_id", "sync_batch_id", "events")}
    from universal_intelligence_engine.security.provenance_crypto import canonical_json
    resigned["batch_signature"] = __import__("base64").b64encode(private.sign(canonical_json(signed_content).encode())).decode()
    with pytest.raises(MeshSecurityViolation, match="record hash"):
        reconcile_offline_batch(resigned, db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM uie_integrity_ledger").fetchone()[0] == 0
