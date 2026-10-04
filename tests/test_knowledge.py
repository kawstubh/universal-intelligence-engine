from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine
from universal_intelligence_engine.knowledge import deduplicate_evidence, evidence_score, rank_evidence


def make_evidence(title, *, authority, confidence=0.5, url=None, content="same"):
    return Evidence(
        source=title,
        title=title,
        retrieved_at="2026-10-05T00:00:00Z",
        content=content,
        url=url,
        confidence=confidence,
        metadata={
            "provenance": {
                "source": title,
                "url": url,
                "retrieved_at": "2026-10-05T00:00:00Z",
                "authority": authority,
            }
        },
    )


def test_rank_evidence_prefers_trusted_sources():
    weak = make_evidence("weak", authority=0.3, confidence=0.4)
    strong = make_evidence("strong", authority=0.9, confidence=0.9)

    ranked = rank_evidence([weak, strong])

    assert ranked[0].source == "strong"
    assert evidence_score(ranked[0]) > evidence_score(ranked[1])


def test_deduplicate_evidence_removes_exact_duplicate():
    first = make_evidence("one", authority=0.8, url="https://example.com/a", content="same claim")
    duplicate = make_evidence("one", authority=0.8, url="https://example.com/a", content="same claim")

    assert len(deduplicate_evidence([first, duplicate])) == 1


class Knowledge:
    def search(self, request):
        return [
            make_evidence("weak", authority=0.2, confidence=0.2),
            make_evidence("strong", authority=0.9, confidence=0.9),
        ]


class Reasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(answer=evidence[0].source, evidence=evidence)


def test_engine_reasons_over_ranked_evidence():
    result = UniversalIntelligenceEngine(Knowledge(), Reasoning()).run(
        IntelligenceRequest(goal="rank")
    )

    assert result.answer == "strong"
    assert result.metadata["evidence_count"] == 2


class FailingProvider:
    def search(self, request):
        raise RuntimeError("temporary provider outage")


class HealthyProvider:
    def search(self, request):
        return [make_evidence("healthy", authority=0.8, confidence=0.8)]


def test_composite_provider_survives_one_provider_failure():
    from universal_intelligence_engine.knowledge import CompositeKnowledgeProvider

    provider = CompositeKnowledgeProvider([FailingProvider(), HealthyProvider()])

    result = provider.search(IntelligenceRequest(goal="research"))

    assert [item.source for item in result] == ["healthy"]
