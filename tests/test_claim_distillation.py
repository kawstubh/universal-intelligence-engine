from universal_intelligence_engine.learning.distiller import ClaimDistiller, distill_claims
from universal_intelligence_engine.learning.contradiction import ContradictionEngine


def test_raw_content_yields_structured_unverified_claim_with_provenance():
    url = "https://www.sec.gov/reports/example"
    content = "The Orion Corporation is headquartered in Mumbai as of 2025. The company has 120 offices worldwide."
    claims = distill_claims(content, url, domain="markets_finance_industry")
    assert claims
    claim = claims[0].to_dict()
    assert {"subject", "predicate", "object_claim", "temporal_anchor", "confidence", "provenance_url", "domain", "status"} <= set(claim)
    assert claim["provenance_url"] == url
    assert claim["domain"] == "markets_finance_industry"
    assert claim["status"] == "unverified_claim"
    assert 0.0 < claim["confidence"] <= 1.0
    assert claim["temporal_anchor"] == "as of 2025"


def test_contradiction_preserves_both_claims_and_logs_conflict(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"), established_confidence=0.80)
    positive = ClaimDistiller().distill("The Orion Corporation is headquartered in Mumbai.", "https://www.sec.gov/a", "markets_finance_industry")[0]
    negative = ClaimDistiller().distill("The Orion Corporation is not headquartered in Mumbai.", "https://www.reuters.com/b", "news_current_affairs")[0]
    first = engine.process_claim(positive)
    second = engine.process_claim(negative)
    assert first["status"] == "unverified_claim"
    assert second["status"] == "conflicted_evidence"
    assert len(engine.get_claims("The Orion Corporation")) == 2
    assert len(engine.contradictions()) == 1
    assert engine.contradictions()[0]["existing_claim_id"] == first["id"]
    assert engine.contradictions()[0]["incoming_claim_id"] == second["id"]


def test_independent_source_corroboration_reinforces_confidence(tmp_path):
    engine = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    first = ClaimDistiller().distill("The Orion Corporation is headquartered in Mumbai.", "https://www.sec.gov/a", "markets_finance_industry")[0]
    second = ClaimDistiller().distill("The Orion Corporation is headquartered in Mumbai.", "https://www.reuters.com/b", "news_current_affairs")[0]
    stored = engine.process_claim(first)
    reinforced = engine.process_claim(second)
    assert reinforced["action"] == "corroborated"
    assert reinforced["confidence"] > stored["confidence"]
    record = engine.get_claims("The Orion Corporation")[0]
    assert len(record["provenance_urls"]) == 2


def test_queue_batch_distills_stored_document_into_claim_memory(tmp_path):
    from unittest.mock import patch
    from universal_intelligence_engine.ingestion.pipeline import CrossDomainIngestionPipeline
    from universal_intelligence_engine.web_knowledge import PublicWebKnowledgeStore

    store = PublicWebKnowledgeStore(str(tmp_path / "corpus.sqlite3"), min_fetch_interval=0)
    pipeline = CrossDomainIngestionPipeline(store, str(tmp_path / "queue.sqlite3"), claim_memory=ContradictionEngine(str(tmp_path / "claims.sqlite3")))
    url = "https://www.sec.gov/reports/example"
    pipeline.enqueue([url], category="markets_finance_industry")
    document = {"content": "The Orion Corporation is headquartered in Mumbai. The Orion Corporation has 120 offices worldwide.", "canonical_url": url}
    with patch.object(store, "ingest_url", return_value={"url": url, "status": "stored"}), patch.object(store, "get_document", return_value=document):
        result = pipeline.run_batch(1)
    assert result["results"][0]["claims_extracted"] >= 1
    assert len(pipeline.claim_memory.get_claims()) >= 1
