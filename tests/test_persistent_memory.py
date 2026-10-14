import pytest

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
