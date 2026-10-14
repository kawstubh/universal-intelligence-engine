import sqlite3

import pytest

from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.query_claims import ClaimQueryService
from universal_intelligence_engine.learning.semantic_matcher import SemanticMatcher, decode_float32
from universal_intelligence_engine.persistent_memory import PersistentMemoryEngine
from universal_intelligence_engine.security.provenance_crypto import (
    clinical_override_payload, sign_clinical_override, verify_clinical_override, verify_memory_chain,
)


def test_hash_chain_detects_tampered_stored_claim(tmp_path):
    db = tmp_path / "claims.sqlite3"
    memory = ContradictionEngine(db)
    claim = DistilledClaim("Periapical tissues", "show", "acute apical abscess swelling", None, 0.6,
                           "https://www.who.int/example", "health_life_sciences")
    stored = memory.process_claim(claim)
    assert memory.verify_chain() == (True, None)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE claims SET object_claim='tampered content' WHERE id=?", (stored["id"],))
    valid, error = memory.verify_chain()
    assert not valid
    assert "differs from chained record" in error


def test_clinical_override_requires_valid_hmac_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("UIE_LESSON_REVIEW_TOKEN", "review-secret")
    monkeypatch.setenv("UIE_DOCTOR_SIGNING_KEY", "doctor-secret")
    memory = PersistentMemoryEngine(tmp_path / "lessons.sqlite3")
    lesson_id = memory.store_lesson("dental", ["acute apical abscess"],
                                    "Review clinical warning signs.", "trusted-review-proposal")
    with pytest.raises(PermissionError, match="HMAC signature is invalid"):
        memory.validate_lesson(lesson_id, "reviewer-1", "review-secret", signature="not-a-valid-signature")
    with sqlite3.connect(tmp_path / "lessons.sqlite3") as conn:
        with pytest.raises(sqlite3.IntegrityError, match="requires HMAC signature"):
            conn.execute("UPDATE uie_lessons SET validation_status='doctor_validated' WHERE id=?", (lesson_id,))
    payload = clinical_override_payload(lesson_id, "dental", "Review clinical warning signs.", "reviewer-1")
    valid_signature = sign_clinical_override(payload, "doctor-secret")
    assert verify_clinical_override(payload, valid_signature, "doctor-secret")
    memory.validate_lesson(lesson_id, "reviewer-1", "review-secret", signature=valid_signature)
    assert memory.verify_override(lesson_id)
    assert memory.recall_lessons("dental", "acute apical abscess")


def test_semantic_discovery_retrieves_paraphrased_claim_and_float32_vector(tmp_path):
    matcher = SemanticMatcher()
    score = matcher.similarity("periapical swelling", "acute apical abscess")
    assert score >= 0.18
    memory = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    memory.process_claim(DistilledClaim("acute apical abscess", "may cause",
        "localized infection and swelling near a tooth root", None, 0.6,
        "https://www.who.int/example", "health_life_sciences"))
    response = ClaimQueryService(memory).query("periapical swelling")
    assert response["count"] == 1
    assert response["claims"][0]["subject"] == "acute apical abscess"
    blob = matcher.persist_vector(tmp_path / "claims.sqlite3", "claim", "c-1", "periapical swelling")
    assert len(blob) == 384 * 4
    assert len(decode_float32(blob)) == 384
    assert verify_memory_chain([{"record_payload":{"x":1},"prev_hash":"0"*64,
                                 "record_hash":__import__("hashlib").sha256(
                                     __import__("json").dumps({"x":1},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+("0"*64).encode()).hexdigest()}]) == (True, None)


def test_hash_chain_detects_tampered_heuristic_rule(tmp_path):
    from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory

    db = tmp_path / "shared.sqlite3"
    memory = ContradictionEngine(db)
    heuristics = HeuristicMemory(db)
    claim = {"subject": "W3C PNG", "predicate": "is", "object_claim": "a public specification",
             "domain": "technology_science", "confidence": 0.7,
             "provenance_urls": ["https://www.w3.org/a", "https://docs.python.org/b"]}
    result = heuristics.consolidate(claim, independent_sources=2)
    assert result["stored"]
    assert memory.verify_chain() == (True, None)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE heuristic_rules SET rule_text='silently altered' WHERE id=?", (result["id"],))
    valid, error = memory.verify_chain()
    assert not valid
    assert "heuristic" in error and "differs from chained record" in error
