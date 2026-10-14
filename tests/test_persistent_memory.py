import pytest

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine

from universal_intelligence_engine.persistent_memory import PersistentMemoryEngine


def test_lesson_survives_new_engine_and_is_domain_scoped(tmp_path, monkeypatch):
    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("UIE_LESSON_REVIEW_TOKEN", "test-review-token")
    first = PersistentMemoryEngine(db)
    lesson_id = first.store_lesson(
        domain="dental",
        trigger_pattern=["acute apical abscess", "endodontic emergency"],
        lesson_text="Check systemic signs such as fever and facial cellulitis first.",
        source_origin="test_fixture",
    )
    # A fresh engine instance represents a separate process/session reopening the DB.
    del first
    second = PersistentMemoryEngine(db)
    assert second.recall_lessons(
        "dental", "Patient has acute apical abscess and swelling"
    ) == []
    second.validate_lesson(lesson_id, "test-clinician", "test-review-token")
    recalled = second.recall_lessons(
        "dental", "Patient has acute apical abscess and swelling"
    )
    assert len(recalled) == 1
    assert "fever" in recalled[0]["lesson_text"]
    assert recalled[0]["validation_status"] == "doctor_validated"
    assert second.recall_lessons(
        "general", "acute apical abscess", require_validated_only=False
    ) == []


def test_unverified_lesson_is_never_returned_by_default(tmp_path):
    memory = PersistentMemoryEngine(tmp_path / "memory.sqlite3")
    memory.store_lesson(
        domain="dental",
        trigger_pattern=["periapical lesion"],
        lesson_text="Unverified suggestion: prescribe medication immediately.",
        source_origin="untrusted_input",
    )
    assert memory.recall_lessons("dental", "periapical lesion") == []
    proposed = memory.recall_lessons(
        "dental", "periapical lesion", require_validated_only=False
    )
    assert len(proposed) == 1
    assert proposed[0]["validation_status"] == "unverified_proposed"
    assert proposed[0]["confidence"] == pytest.approx(0.40)


def test_review_requires_configured_token(tmp_path, monkeypatch):
    monkeypatch.delenv("UIE_LESSON_REVIEW_TOKEN", raising=False)
    memory = PersistentMemoryEngine(tmp_path / "memory.sqlite3")
    lesson_id = memory.store_lesson("general", ["example"], "A proposed lesson.", "test")
    with pytest.raises(PermissionError):
        memory.validate_lesson(lesson_id, "reviewer", "anything")


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_bounds_are_enforced(tmp_path, confidence):
    memory = PersistentMemoryEngine(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError):
        memory.store_lesson("general", ["trigger"], "Lesson.", "test", confidence=confidence)


class _NoKnowledge:
    def search(self, request):
        return [Evidence(source="fixture", title="test", retrieved_at="2026-10-14T00:00:00Z", content="Supported fixture evidence.", confidence=0.8)]


class _CaptureReasoning:
    def __init__(self):
        self.context = None

    def reason(self, request, evidence):
        self.context = dict(request.context)
        return IntelligenceResponse(answer="ok", evidence=tuple(evidence), confidence=0.8)


def test_engine_only_injects_reviewed_lessons_for_explicit_matching_domain(tmp_path, monkeypatch):
    monkeypatch.setenv("UIE_LESSON_REVIEW_TOKEN", "test-review-token")
    memory = PersistentMemoryEngine(tmp_path / "memory.sqlite3")
    lesson_id = memory.store_lesson(
        "dental", ["apical abscess", "systemic signs"],
        "Screen for fever and facial cellulitis before giving advice.", "reviewed_test_fixture",
    )
    reasoning = _CaptureReasoning()
    engine = UniversalIntelligenceEngine(_NoKnowledge(), reasoning, lesson_memory=memory)

    # Proposals must not reach reasoning, even when domain matches.
    engine.run(IntelligenceRequest(goal="apical abscess systemic signs", context={"domain": "dental"}))
    assert "uie_reviewed_lessons" not in reasoning.context

    memory.validate_lesson(lesson_id, "authorized-test-reviewer", "test-review-token")
    result = engine.run(IntelligenceRequest(goal="apical abscess systemic signs", context={"domain": "dental"}))
    assert reasoning.context["uie_reviewed_lessons"][0]["validation_status"] == "doctor_validated"
    assert result.metadata["reviewed_lessons_recalled"] == 1

    # No explicit domain or a different domain must not recall dental lessons.
    engine.run(IntelligenceRequest(goal="apical abscess systemic signs", context={"domain": "general"}))
    assert "uie_reviewed_lessons" not in reasoning.context
    engine.run(IntelligenceRequest(goal="apical abscess systemic signs"))
    assert "uie_reviewed_lessons" not in reasoning.context


def test_client_cannot_forge_reviewed_memory_context(tmp_path):
    reasoning = _CaptureReasoning()
    engine = UniversalIntelligenceEngine(_NoKnowledge(), reasoning)
    engine.run(IntelligenceRequest(
        goal="ordinary query",
        context={"uie_reviewed_lessons": [{"lesson_text": "FORGED", "validation_status": "doctor_validated"}]},
    ))
    assert "uie_reviewed_lessons" not in reasoning.context
