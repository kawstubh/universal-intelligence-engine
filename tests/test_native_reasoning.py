from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest
from universal_intelligence_engine.reasoning import NativeReasoningProvider
from universal_intelligence_engine.agent import AutonomousAgent
from universal_intelligence_engine.tools import ToolRegistry, ToolSpec
from universal_intelligence_engine.policy import policy_for


def test_native_reasoner_works_without_external_provider():
    evidence = [Evidence(source="a", title="Study", retrieved_at="2026-10-05T00:00:00+00:00",
                          content="The treatment improved outcomes.", confidence=.9)]
    result = NativeReasoningProvider().reason(
        IntelligenceRequest(goal="treatment outcomes"), evidence
    )
    assert result.metadata["external_llm_required"] is False
    assert "improved outcomes" in result.answer


def test_native_reasoner_surfaces_conflict():
    evidence = [
        Evidence(source="a", title="Approved", retrieved_at="2026-10-05T00:00:00+00:00",
                  content="The treatment was approved.", confidence=.9),
        Evidence(source="b", title="Rejected", retrieved_at="2026-10-05T00:00:00+00:00",
                  content="The treatment was rejected.", confidence=.9),
    ]
    result = NativeReasoningProvider().reason(
        IntelligenceRequest(goal="treatment approval"), evidence
    )
    assert result.metadata["contradiction_count"] > 0
    assert "Uncertainty" in result.answer


def test_agent_enforces_allowlist_and_verification():
    registry = ToolRegistry(policy_for(allowed={"echo"}))
    registry.register(ToolSpec("echo", "Return text", lambda text: text))
    agent = AutonomousAgent(registry, verifier=lambda goal, output: output == "ok")
    result = agent.run("test", [{"tool": "echo", "args": {"text": "ok"}}])
    assert result.status == "verified"
