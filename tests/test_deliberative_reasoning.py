from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.reasoning import DeliberationController, ProcessRewardEvaluator, UIEReasoningCoordinator


def _evidence(source, title, content, confidence=.8, domain=None):
    metadata = {"domain": domain} if domain else {}
    return Evidence(source=source, title=title, retrieved_at="2026-10-14T00:00:00+00:00", content=content,
                    confidence=confidence, metadata=metadata)


class CaptureProvider:
    def __init__(self):
        self.evidence = None

    def reason(self, request, evidence):
        self.evidence = tuple(evidence)
        return IntelligenceResponse(answer="grounded answer", evidence=tuple(evidence), confidence=.82,
                                    metadata={"provider": "test"})


def test_deliberation_generates_multiple_paths_and_scores_intermediate_steps():
    request = IntelligenceRequest(goal="compare battery life and processor performance across devices")
    evidence = [
        _evidence("spec-a", "Battery life", "Device A battery lasts 12 hours with a 5000 mAh battery.", .9, "hardware"),
        _evidence("spec-b", "Processor performance", "Device B processor benchmark scores 1800 points.", .88, "hardware"),
        _evidence("review-c", "Battery benchmark", "Device C battery lasted 10 hours in the independent test.", .86, "reviews"),
    ]
    controller = DeliberationController()
    budget = controller.budget(request, evidence)
    result = controller.search(request, evidence, budget)
    assert budget.enabled
    assert result["trajectory_count"] >= 2
    assert all("step_reward" in step for trajectory in result["trajectories"] for step in trajectory["steps"])
    assert all(0 <= trajectory["score"] <= 1 for trajectory in result["trajectories"])


def test_coordinator_selects_highest_reward_verified_trajectory_before_synthesis():
    request = IntelligenceRequest(goal="compare treatment approval status and evidence across studies")
    evidence = [
        _evidence("trusted-a", "Approval status", "The treatment was approved by the regulator in 2025.", .95),
        _evidence("trusted-b", "Study outcomes", "The treatment improved outcomes in the controlled study.", .92),
        _evidence("weak-c", "Unrelated weather", "Rainfall increased in the northern region.", .15),
    ]
    provider = CaptureProvider()
    response = UIEReasoningCoordinator().reason(request, evidence, provider)
    info = response.metadata["deliberation"]
    assert info["trajectory_count"] > 1
    assert info["selected_verified"] is True
    assert info["selection_reason"] == "highest_reward_verified_trajectory"
    assert len(provider.evidence) == info["selected_evidence_count"]
    assert all(item.source != "weak-c" for item in provider.evidence)
    assert response.metadata["reasoning_mode"] == "test_time_deliberation"


def test_dynamic_budget_bypasses_search_for_direct_lookup():
    request = IntelligenceRequest(goal="capital of France")
    evidence = [_evidence("reference", "Capital", "Paris is the capital of France.", .99)]
    result = DeliberationController().search(request, evidence)
    assert result["enabled"] is False
    assert result["trajectory_count"] == 0
    assert result["budget"]["reason"] == "direct_lookup"


def test_prm_marks_conflicting_trajectory_unverified():
    evaluator = ProcessRewardEvaluator()
    path = [
        _evidence("a", "Approval", "The treatment was approved by the regulator.", .9),
        _evidence("b", "Approval", "The treatment was rejected by the regulator.", .9),
    ]
    _, steps, signals = evaluator.score_trajectory("treatment approval status", path)
    assert all(step["grounded"] for step in steps)
    assert signals["contradictions"] > 0
    assert signals["verified"] is False


def test_engine_wires_deliberation_into_complex_queries_and_episode_recording():
    from universal_intelligence_engine.engine import UniversalIntelligenceEngine

    class Knowledge:
        def search(self, request):
            return [
                _evidence("study-a", "Safety results", "Study A found improved safety and outcomes.", .9),
                _evidence("study-b", "Effectiveness results", "Study B found improved effectiveness outcomes.", .88),
            ]

    class Learning:
        def __init__(self):
            self.events = []
        def record(self, event):
            self.events.append(dict(event))

    learning = Learning()
    engine = UniversalIntelligenceEngine(Knowledge(), CaptureProvider(), learning_store=learning)
    result = engine.run(IntelligenceRequest(goal="compare safety and effectiveness outcomes across studies"))
    assert result.metadata["reasoning_mode"] == "test_time_deliberation"
    assert result.metadata["deliberation"]["trajectory_count"] > 0
    assert learning.events[0]["reasoning_mode"] == "test_time_deliberation"
    assert learning.events[0]["deliberation"]["trajectory_count"] > 0
