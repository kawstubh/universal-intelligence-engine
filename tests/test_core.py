from universal_intelligence_engine.locale import resolve_locale
from universal_intelligence_engine.memory import LearningEvent, LearningMemory
from universal_intelligence_engine.provenance import ProvenanceRecord


def test_locale_supports_regional_and_rtl():
    locale = resolve_locale("ar-AE")
    assert locale.language == "ar"
    assert locale.region == "AE"
    assert locale.rtl is True


def test_provenance_trust_accounts_for_corroboration_and_contradiction():
    record = ProvenanceRecord(
        source="test",
        url=None,
        retrieved_at="2026-10-04T00:00:00Z",
        authority=0.8,
        corroboration=2,
        contradiction=1,
    )
    assert round(record.trust_score(), 2) == 0.80


def test_learning_memory_is_controlled(tmp_path):
    path = tmp_path / "learning.jsonl"
    memory = LearningMemory(path)
    memory.record(LearningEvent("research", "success", 1.0, {"locale": "en-IN"}))
    summary = memory.summarize()
    assert summary["research"]["count"] == 1
    assert summary["research"]["average_reward"] == 1.0
