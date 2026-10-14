from universal_intelligence_engine import providers
from universal_intelligence_engine.contracts import IntelligenceRequest


def test_free_public_provider_uses_europe_pmc_for_medical_and_wikipedia(monkeypatch):
    requested_urls = []

    def fake_request(url, **kwargs):
        requested_urls.append(url)
        if "europepmc" in url:
            return {
                "resultList": {
                    "result": [
                        {
                            "title": "Dental research study",
                            "abstractText": "Study abstract",
                            "pubYear": "2025",
                            "journalTitle": "Dental Journal",
                            "pmid": "123456",
                        }
                    ]
                }
            }
        return {
            "query": {
                "search": [
                    {
                        "title": "Dental caries",
                        "pageid": 987,
                        "snippet": "A <span>common</span> oral disease &amp; condition.",
                    }
                ]
            }
        }

    monkeypatch.setattr(providers, "_json_request", fake_request)
    evidence = providers.FreePublicKnowledgeProvider().search(
        IntelligenceRequest(goal="dental caries evidence", locale="IN-MH", language="en")
    )

    assert len(evidence) == 2
    assert evidence[0].source == "europe-pmc-public-api"
    assert evidence[0].url == "https://europepmc.org/article/MED/123456"
    assert evidence[0].metadata["publication_year"] == "2025"
    assert evidence[1].source == "wikipedia-public-api"
    assert evidence[1].url == "https://en.wikipedia.org/?curid=987"
    assert evidence[1].content == "A common oral disease & condition."
    assert any("europepmc" in url for url in requested_urls)
    assert any("wikipedia.org" in url for url in requested_urls)


def test_free_public_provider_returns_no_fabricated_evidence_when_sources_fail(monkeypatch):
    def fail_request(url, **kwargs):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(providers, "_json_request", fail_request)
    evidence = providers.FreePublicKnowledgeProvider().search(
        IntelligenceRequest(goal="dental caries evidence")
    )

    assert evidence == []


def test_free_public_provider_general_query_uses_wikipedia_only(monkeypatch):
    requested_urls = []

    def fake_request(url, **kwargs):
        requested_urls.append(url)
        return {"query": {"search": [{"title": "Photosynthesis", "pageid": 24544, "snippet": "Plants convert light into chemical energy."}]}}

    monkeypatch.setattr(providers, "_json_request", fake_request)
    evidence = providers.FreePublicKnowledgeProvider().search(
        IntelligenceRequest(goal="photosynthesis", language="en")
    )

    assert len(evidence) == 1
    assert evidence[0].source == "wikipedia-public-api"
    assert all("europepmc" not in url for url in requested_urls)
