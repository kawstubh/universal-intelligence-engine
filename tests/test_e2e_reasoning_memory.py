from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.engine import UniversalIntelligenceEngine
from universal_intelligence_engine.persistent_memory import PersistentMemoryEngine


class Knowledge:
    def search(self, request):
        return [Evidence(source="fixture", title="Fixture", retrieved_at="2026-10-14T00:00:00Z", content="Evidence.", confidence=0.9)]


class CaptureReasoner:
    def __init__(self):
        self.request = None

    def reason(self, request, evidence):
        self.request = request
        return IntelligenceResponse(answer="ok", evidence=tuple(evidence), confidence=0.9)


def test_end_to_end_reviewed_memory_is_injected_and_traced(tmp_path, monkeypatch):
    monkeypatch.setenv("UIE_LESSON_REVIEW_TOKEN", "test-token")
    memory = PersistentMemoryEngine(tmp_path / "e2e.sqlite3")
    lesson_id = memory.store_lesson("dental", ["periapical abscess", "swelling"], "Check systemic red flags before intervention.", "clinician_fixture")
    reasoner = CaptureReasoner()
    engine = UniversalIntelligenceEngine(Knowledge(), reasoner, lesson_memory=memory)
    request = IntelligenceRequest(goal="periapical abscess and jaw swelling", context={"domain": "dental"})

    unreviewed = engine.run(request)
    assert unreviewed.metadata["applied_lesson_ids"] == []
    assert "uie_reviewed_lessons" not in reasoner.request.context

    memory.validate_lesson(lesson_id, "test-reviewer", "test-token")
    result = engine.run(request)
    assert result.metadata["applied_lesson_ids"] == [lesson_id]
    assert "Check systemic red flags" in reasoner.request.context["uie_reviewed_lessons"][0]["lesson_text"]

    general = engine.run(IntelligenceRequest(goal=request.goal, context={"domain": "general"}))
    assert general.metadata["applied_lesson_ids"] == []
    assert "uie_reviewed_lessons" not in reasoner.request.context
