"""Ed25519-signed offline mesh batches re-anchored into UIE's append-only ledger."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .provenance_crypto import GENESIS_HASH, append_integrity_event, canonical_json, ensure_integrity_ledger, verify_integrity_ledger

MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_BATCH_EVENTS = 10_000
MESH_SCHEMA = """
CREATE TABLE IF NOT EXISTS uie_mesh_node_keys (
  node_id TEXT PRIMARY KEY,
  public_key_pem TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS uie_mesh_ingested_events (
  node_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  sync_batch_id TEXT NOT NULL,
  ledger_sequence INTEGER,
  incoming_record_hash TEXT NOT NULL,
  PRIMARY KEY(node_id,event_id)
);
CREATE TABLE IF NOT EXISTS uie_mesh_batches (
  node_id TEXT NOT NULL,
  sync_batch_id TEXT NOT NULL,
  bundle_digest TEXT NOT NULL,
  accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(node_id,sync_batch_id)
);
"""


class MeshSecurityViolation(ValueError):
    """Offline batch signature, schema, or hash-chain validation failed."""


@dataclass(frozen=True)
class SyncResult:
    node_id: str
    sync_batch_id: str
    accepted: bool
    appended_events: int
    skipped_duplicates: int
    ledger_sequences: list[int] = field(default_factory=list)
    ledger_verified: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "sync_batch_id": self.sync_batch_id,
                "accepted": self.accepted, "appended_events": self.appended_events,
                "skipped_duplicates": self.skipped_duplicates,
                "ledger_sequences": self.ledger_sequences,
                "ledger_verified": self.ledger_verified, "message": self.message}


def _event_hash(node_id: str, sequence: int, event_id: str, event_type: str,
                payload: Mapping[str, Any], previous_hash: str) -> str:
    core = {"node_id": node_id, "sequence": sequence, "event_id": event_id,
            "event_type": event_type, "payload": payload}
    return hashlib.sha256(canonical_json(core).encode("utf-8") + previous_hash.encode("ascii")).hexdigest()


def register_node_public_key(db_path: str | Path, node_id: str, public_key_pem: bytes | str) -> None:
    """Register an Ed25519 public key through an administrative/trusted channel."""
    if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
        raise ValueError("node_id must be a non-empty string up to 256 characters")
    raw = public_key_pem.encode("utf-8") if isinstance(public_key_pem, str) else public_key_pem
    try:
        key = serialization.load_pem_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("public key must be valid PEM") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("only Ed25519 public keys are accepted")
    pem = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.executescript(MESH_SCHEMA)
        conn.execute("INSERT INTO uie_mesh_node_keys(node_id,public_key_pem,enabled) VALUES(?,?,1) "
                     "ON CONFLICT(node_id) DO UPDATE SET public_key_pem=excluded.public_key_pem,enabled=1",
                     (node_id, pem))


def create_signed_sync_bundle(node_id: str, sync_batch_id: str, events: list[dict[str, Any]],
                              private_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Build canonical offline events and sign the complete bundle with a node Ed25519 key."""
    if not node_id or not sync_batch_id or len(events) > MAX_BATCH_EVENTS:
        raise ValueError("node_id, sync_batch_id and a bounded events list are required")
    chain = []
    previous = GENESIS_HASH
    for sequence, raw in enumerate(events, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("each event must be a mapping")
        event_id = raw.get("event_id") or str(uuid.uuid4())
        event_type = raw.get("event_type", "offline_observation")
        payload = raw.get("payload", {})
        if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
            raise ValueError("event_id must be a non-empty string up to 256 characters")
        if not isinstance(event_type, str) or not event_type or len(event_type) > 256 or not isinstance(payload, Mapping):
            raise ValueError("invalid event_type or payload")
        event = {"sequence": sequence, "event_id": event_id, "event_type": event_type,
                 "payload": dict(payload), "prev_hash": previous}
        event["record_hash"] = _event_hash(node_id, sequence, event_id, event_type, event["payload"], previous)
        chain.append(event)
        previous = event["record_hash"]
    signed_content = {"node_id": node_id, "sync_batch_id": sync_batch_id, "events": chain}
    encoded = canonical_json(signed_content).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds maximum size")
    signature = base64.b64encode(private_key.sign(encoded)).decode("ascii")
    return {**signed_content, "batch_signature": signature}


def reconcile_offline_batch(sync_bundle: dict[str, Any], db_path: str | Path) -> SyncResult:
    """Validate first, then append signed events; historical ledger hashes are never rewritten."""
    if not isinstance(sync_bundle, dict):
        raise MeshSecurityViolation("sync bundle must be a dictionary")
    try:
        encoded_bundle = canonical_json(sync_bundle).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MeshSecurityViolation("bundle is not canonicalizable JSON") from exc
    if len(encoded_bundle) > MAX_BUNDLE_BYTES:
        raise MeshSecurityViolation("bundle exceeds maximum size")
    node_id, batch_id = sync_bundle.get("node_id"), sync_bundle.get("sync_batch_id")
    events, signature = sync_bundle.get("events"), sync_bundle.get("batch_signature")
    if not isinstance(node_id, str) or not node_id or not isinstance(batch_id, str) or not batch_id:
        raise MeshSecurityViolation("node_id and sync_batch_id are required")
    if not isinstance(events, list) or len(events) > MAX_BATCH_EVENTS:
        raise MeshSecurityViolation("events must be a bounded list")
    if not isinstance(signature, str):
        raise MeshSecurityViolation("batch_signature is required")

    db_path = str(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True) if db_path != ":memory:" else None
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        ensure_integrity_ledger(conn)
        conn.executescript(MESH_SCHEMA)
        key_row = conn.execute("SELECT public_key_pem FROM uie_mesh_node_keys WHERE node_id=? AND enabled=1", (node_id,)).fetchone()
        if not key_row:
            raise MeshSecurityViolation("node has no registered signing authority")
        try:
            public_key = serialization.load_pem_public_key(key_row["public_key_pem"].encode("ascii"))
            if not isinstance(public_key, Ed25519PublicKey):
                raise MeshSecurityViolation("registered node key is not Ed25519")
            signed_content = {"node_id": node_id, "sync_batch_id": batch_id, "events": events}
            public_key.verify(base64.b64decode(signature, validate=True), canonical_json(signed_content).encode("utf-8"))
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise MeshSecurityViolation("batch signature is invalid") from exc

        previous = GENESIS_HASH
        event_ids = set()
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise MeshSecurityViolation(f"event {index} must be an object")
            required = {"sequence", "event_id", "event_type", "payload", "prev_hash", "record_hash"}
            if set(event) != required:
                raise MeshSecurityViolation(f"event {index} has missing or unexpected fields")
            if event["sequence"] != index or event["prev_hash"] != previous:
                raise MeshSecurityViolation(f"event {index} has broken sequence or hash-chain linkage")
            if not isinstance(event["event_id"], str) or not event["event_id"] or event["event_id"] in event_ids:
                raise MeshSecurityViolation(f"event {index} has duplicate or invalid event_id")
            if not isinstance(event["event_type"], str) or not isinstance(event["payload"], dict):
                raise MeshSecurityViolation(f"event {index} has invalid event_type or payload")
            expected = _event_hash(node_id, index, event["event_id"], event["event_type"], event["payload"], previous)
            if not hmac.compare_digest(str(event["record_hash"]), expected):
                raise MeshSecurityViolation(f"event {index} record hash mismatch")
            event_ids.add(event["event_id"])
            previous = expected

        valid, error = verify_integrity_ledger(conn)
        if not valid:
            raise MeshSecurityViolation(f"primary ledger is already invalid: {error}")
        digest = hashlib.sha256(encoded_bundle).hexdigest()
        old_batch = conn.execute("SELECT bundle_digest FROM uie_mesh_batches WHERE node_id=? AND sync_batch_id=?",
                                 (node_id, batch_id)).fetchone()
        if old_batch:
            if not hmac.compare_digest(old_batch["bundle_digest"], digest):
                raise MeshSecurityViolation("sync_batch_id was reused with different bundle content")
            return SyncResult(node_id, batch_id, True, 0, len(events), [], True, "batch already reconciled")

        # Validate everything before any writes. All appends and idempotency rows share one transaction.
        conn.execute("BEGIN IMMEDIATE")
        appended, duplicates, sequences = 0, 0, []
        for event in events:
            existing = conn.execute("SELECT incoming_record_hash,ledger_sequence FROM uie_mesh_ingested_events WHERE node_id=? AND event_id=?",
                                    (node_id, event["event_id"])).fetchone()
            if existing:
                if not hmac.compare_digest(existing["incoming_record_hash"], event["record_hash"]):
                    raise MeshSecurityViolation("event_id was reused with different content")
                duplicates += 1
                continue
            audit_payload = {"node_id": node_id, "sync_batch_id": batch_id,
                             "offline_event": event, "status": "reconciled"}
            ledger_event = append_integrity_event(conn, "offline_mesh_event", f"{node_id}:{event['event_id']}",
                                                  event["event_type"], audit_payload)
            sequence = int(ledger_event["sequence_id"])
            conn.execute("INSERT INTO uie_mesh_ingested_events(node_id,event_id,sync_batch_id,ledger_sequence,incoming_record_hash) VALUES(?,?,?,?,?)",
                         (node_id, event["event_id"], batch_id, sequence, event["record_hash"]))
            appended += 1
            sequences.append(sequence)
        conn.execute("INSERT INTO uie_mesh_batches(node_id,sync_batch_id,bundle_digest) VALUES(?,?,?)",
                     (node_id, batch_id, digest))
        valid, error = verify_integrity_ledger(conn)
        if not valid:
            raise MeshSecurityViolation(f"ledger verification failed after reconciliation: {error}")
        return SyncResult(node_id, batch_id, True, appended, duplicates, sequences, True, "batch reconciled and ledger verified")
