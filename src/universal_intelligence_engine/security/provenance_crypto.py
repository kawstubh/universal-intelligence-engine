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
