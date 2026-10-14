"""Tamper-evident SHA-256 memory chains and HMAC clinical-override signatures."""
from __future__ import annotations
import hashlib, hmac, json, os
from typing import Any, Iterable
GENESIS_HASH = "0" * 64

def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

def calculate_record_hash(record_payload: Any, prev_hash: str = GENESIS_HASH) -> str:
    if not isinstance(prev_hash, str) or len(prev_hash) != 64:
        raise ValueError("prev_hash must be a 64-character SHA-256 hex digest")
    return hashlib.sha256(canonical_json(record_payload).encode("utf-8") + prev_hash.encode("ascii")).hexdigest()

def verify_memory_chain(records: Iterable[dict]) -> tuple[bool, str | None]:
    previous = GENESIS_HASH
    for index, record in enumerate(records):
        payload = record.get("record_payload", record.get("payload"))
        if payload is None:
            encoded = record.get("payload_json")
            if not isinstance(encoded, str): return False, f"record {index}: missing payload"
            try: payload = json.loads(encoded)
            except (TypeError, json.JSONDecodeError): return False, f"record {index}: invalid payload JSON"
        if record.get("prev_hash") != previous: return False, f"record {index}: prev_hash linkage mismatch"
        try: expected = calculate_record_hash(payload, previous)
        except (TypeError, ValueError): return False, f"record {index}: payload cannot be canonicalized"
        if not hmac.compare_digest(str(record.get("record_hash", "")), expected):
            return False, f"record {index}: record_hash mismatch"
        previous = expected
    return True, None

def clinical_override_payload(lesson_id: str, domain: str, lesson_text: str, reviewer_id: str) -> dict:
    return {"lesson_id": lesson_id, "domain": domain.strip().lower(), "lesson_text": lesson_text.strip(),
            "reviewer_id": reviewer_id.strip(), "status": "doctor_validated"}

def sign_clinical_override(payload: dict, key: str | bytes | None = None) -> str:
    secret = key if key is not None else os.getenv("UIE_DOCTOR_SIGNING_KEY")
    if isinstance(secret, str): secret = secret.encode("utf-8")
    if not secret: raise ValueError("clinical override signing key is not configured")
    return hmac.new(secret, canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()

def verify_clinical_override(payload: dict, signature: str, key: str | bytes | None = None) -> bool:
    try: expected = sign_clinical_override(payload, key)
    except ValueError: return False
    return bool(signature) and hmac.compare_digest(expected, signature)


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS uie_integrity_ledger (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_canonical_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    hmac_signature TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ledger_entity ON uie_integrity_ledger(entity_type, entity_id);
"""

def ensure_integrity_ledger(conn) -> None:
    conn.executescript(LEDGER_SCHEMA)

def append_integrity_event(conn, entity_type: str, entity_id: str, event_type: str,
                           payload: dict, hmac_signature: str | None = None,
                           signing_key: str | bytes | None = None) -> dict:
    """Append an immutable, globally chained audit event to an SQLite connection."""
    ensure_integrity_ledger(conn)
    if payload.get("status") == "doctor_validated" and not hmac_signature:
        raise ValueError("doctor_validated status requires a valid HMAC signature")
    if hmac_signature and payload.get("validation_status", payload.get("status")) == "doctor_validated":
        clinical_payload = payload if payload.get("status") == "doctor_validated" else clinical_override_payload(str(payload.get("id", payload.get("lesson_id", ""))), str(payload.get("domain", "")), str(payload.get("lesson_text", "")), str(payload.get("reviewer_id", "")))
        if not verify_clinical_override(clinical_payload, hmac_signature, signing_key):
            raise ValueError("doctor_validated status HMAC signature is invalid")
    previous_row = conn.execute("SELECT sequence_id,record_hash FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1").fetchone()
    previous = previous_row["record_hash"] if previous_row else GENESIS_HASH
    sequence_id = (int(previous_row["sequence_id"]) + 1) if previous_row else 1
    payload_json = canonical_json(payload)
    hash_payload = {"sequence_id": sequence_id, "entity_type": entity_type, "entity_id": str(entity_id),
                    "event_type": event_type, "payload": json.loads(payload_json)}
    digest = hashlib.sha256(canonical_json(hash_payload).encode("utf-8") + previous.encode("ascii")).hexdigest()
    conn.execute("INSERT INTO uie_integrity_ledger(sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash,hmac_signature) VALUES(?,?,?,?,?,?,?,?)",
                 (sequence_id, entity_type, str(entity_id), event_type, payload_json, previous, digest, hmac_signature))
    return {"sequence_id": sequence_id, "prev_hash": previous, "record_hash": digest}

def verify_integrity_ledger(conn) -> tuple[bool, str | None]:
    """Verify sequential hashes, HMAC-gated clinical transitions, and legal entity state transitions."""
    ensure_integrity_ledger(conn)
    rows = conn.execute("SELECT * FROM uie_integrity_ledger ORDER BY sequence_id").fetchall()
    previous = GENESIS_HASH
    expected_sequence = 1
    latest_status: dict[tuple[str, str], str] = {}
    for row in rows:
        item = dict(row)
        seq = int(item["sequence_id"])
        if seq != expected_sequence:
            return False, f"ledger sequence gap: expected {expected_sequence}, found {seq}"
        if item["prev_hash"] != previous:
            return False, f"ledger sequence {seq}: prev_hash linkage mismatch"
        try:
            payload = json.loads(item["payload_canonical_json"])
            if canonical_json(payload) != item["payload_canonical_json"]:
                return False, f"ledger sequence {seq}: payload is not canonical JSON"
            hash_payload = {"sequence_id": seq, "entity_type": item["entity_type"], "entity_id": item["entity_id"],
                            "event_type": item["event_type"], "payload": payload}
            digest = hashlib.sha256(canonical_json(hash_payload).encode("utf-8") + previous.encode("ascii")).hexdigest()
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, f"ledger sequence {seq}: invalid payload JSON"
        if not hmac.compare_digest(str(item["record_hash"]), digest):
            return False, f"ledger sequence {seq}: record_hash mismatch (payload or ledger tampering detected)"
        if payload.get("status") == "doctor_validated" or payload.get("validation_status") == "doctor_validated":
            clinical_payload = payload if payload.get("status") == "doctor_validated" else clinical_override_payload(str(payload.get("id", payload.get("lesson_id", ""))), str(payload.get("domain", "")), str(payload.get("lesson_text", "")), str(payload.get("reviewer_id", "")))
            if not item.get("hmac_signature") or not verify_clinical_override(clinical_payload, item["hmac_signature"], os.getenv("UIE_DOCTOR_SIGNING_KEY") or os.getenv("UIE_LESSON_REVIEW_TOKEN")):
                return False, f"ledger sequence {seq}: doctor_validated status has missing or invalid HMAC signature"
        key = (item["entity_type"], item["entity_id"])
        if item["event_type"] == "SUPERSEDED_TEMPORAL":
            prior = latest_status.get(key)
            if prior in (None, "superseded_temporal", "rejected"):
                return False, f"ledger sequence {seq}: invalid SUPERSEDED_TEMPORAL transition from {prior!r}"
            if payload.get("status") != "superseded_temporal":
                return False, f"ledger sequence {seq}: supersession payload has invalid status"
        if item["event_type"] == "REJECT" and payload.get("status") != "rejected":
            return False, f"ledger sequence {seq}: REJECT payload has invalid status"
        if "status" in payload:
            latest_status[key] = payload["status"]
        previous = digest
        expected_sequence += 1
    return True, None
