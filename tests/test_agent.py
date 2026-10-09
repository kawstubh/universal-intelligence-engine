from universal_intelligence_engine.agent import AutonomousAgent
from universal_intelligence_engine.policy import policy_for
from universal_intelligence_engine.tools import ToolRegistry, ToolSpec


def test_goal_agent_never_executes_planned_action_without_human_approval():
    executed = []
    registry = ToolRegistry(policy_for(allowed=["reason", "verify", "act"]))
    registry.register(ToolSpec("reason", "Reason", lambda **kwargs: "reasoned"))
    registry.register(ToolSpec("verify", "Verify", lambda **kwargs: "verified"))
    registry.register(
        ToolSpec("act", "Publish or mutate an external system", lambda **kwargs: executed.append(kwargs))
    )

    result = AutonomousAgent(registry).run_goal("publish a report")

    assert result.status == "approval_required"
    assert result.metadata["reason"] == "human_approval_required"
    assert result.metadata["pending_action"] == "act"
    assert executed == []
    assert result.steps[-1].status == "approval_required"


def test_goal_agent_can_finish_non_mutating_goal():
    registry = ToolRegistry(policy_for(allowed=["reason", "verify"]))
    registry.register(ToolSpec("reason", "Reason", lambda **kwargs: "draft"))
    registry.register(ToolSpec("verify", "Verify", lambda **kwargs: "checked"))

    result = AutonomousAgent(registry).run_goal("explain a concept")

    assert result.status == "verified"
    assert result.metadata["verified"] is True
