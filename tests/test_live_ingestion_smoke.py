"""Opt-in bounded live smoke test; never part of the default offline test suite."""
import os

import pytest

from universal_intelligence_engine.ingestion.pipeline import CrossDomainIngestionPipeline
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.web_knowledge import PublicWebKnowledgeStore

pytestmark = [pytest.mark.integration]


@pytest.mark.skipif(os.getenv("UIE_RUN_LIVE_SMOKE") != "1", reason="set UIE_RUN_LIVE_SMOKE=1 to authorize a real public-source fetch")
def test_live_w3c_ingest_distill_store_flow(tmp_path):
    """Fetch one approved W3C static document through the production safety policy."""
    url = "https://www.w3.org/TR/PNG/"
    store = PublicWebKnowledgeStore(str(tmp_path / "corpus.sqlite3"), min_fetch_interval=0)
    memory = ContradictionEngine(str(tmp_path / "claims.sqlite3"))
    pipeline = CrossDomainIngestionPipeline(store, str(tmp_path / "queue.sqlite3"), claim_memory=memory)

    assert store._validate_url(url)[0] == "www.w3.org"
    queued = pipeline.enqueue([url], category="technology_science")
    assert queued["queued"] == 1 and queued["rejected"] == 0

    result = pipeline.run_batch(limit=1)
    assert result["processed"] == 1
    outcome = result["results"][0]
    assert outcome["status"] == "stored", outcome

    document = store.get_document(url)
    assert document and len(document["content"]) >= 200
    assert len(document["content"].encode("utf-8")) <= 80_000 * 4
    assert document["canonical_url"].startswith("https://www.w3.org/")
    claims = memory.get_claims()
    assert claims, "Live W3C page was stored but no structured claims were extracted"
    assert all(c["provenance_url"].startswith("https://www.w3.org/") for c in claims)
    assert all(c["status"] == "unverified_claim" for c in claims)
    assert outcome["claims_extracted"] == len(claims)
