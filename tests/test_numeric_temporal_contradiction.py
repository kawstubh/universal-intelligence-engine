from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.query_claims import ClaimQueryService


def claim(obj, url, year=None, confidence=0.84):
    return DistilledClaim(
        subject="Orion Corporation", predicate="reported", object_claim=obj,
        temporal_anchor=year, confidence=confidence, provenance_url=url,
        domain="markets_finance_industry", status="unverified_claim",
    )


def test_numeric_mismatch_logs_numeric_discrepancy(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    engine.process_claim(claim("$4.2B annual revenue", "https://sec.gov/a"))
    result = engine.process_claim(claim("$3.8B annual revenue", "https://reuters.com/b"))
    assert result["status"] == "conflicted_evidence"
    assert result["detail_tag"] == "numeric_discrepancy"
    assert engine.contradictions()[0]["detail_tag"] == "numeric_discrepancy"


def test_numeric_unit_mismatch_detected_for_same_unit(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    engine.process_claim(claim("15 mg daily dose", "https://who.int/a"))
    result = engine.process_claim(claim("30 mg daily dose", "https://cdc.gov/b"))
    assert result["detail_tag"] == "numeric_discrepancy"


def test_temporal_revision_supersedes_older_claim(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    old = engine.process_claim(claim("$4.2B annual revenue", "https://sec.gov/a", "as of 2024"))
    new = engine.process_claim(claim("$3.8B annual revenue", "https://sec.gov/b", "as of 2026"))
    assert new["action"] == "temporal_revision"
    records = engine.get_claims("Orion Corporation")
    assert next(c for c in records if c["id"] == old["id"])["status"] == "superseded_temporal"
    assert next(c for c in records if c["id"] == new["id"])["status"] == "unverified_claim"


def test_claim_query_surfaces_conflict_and_unverified_warnings(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    engine.process_claim(claim("$4.2B annual revenue", "https://sec.gov/a"))
    engine.process_claim(claim("$3.8B annual revenue", "https://reuters.com/b"))
    result = ClaimQueryService(engine).query("Orion Corporation")
    assert result["count"] == 2
    assert len(result["warnings"]) == 2
    assert all(item["warning"] for item in result["claims"])
    assert all(item["provenance_urls"] for item in result["claims"])
