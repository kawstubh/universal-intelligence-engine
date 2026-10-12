from universal_intelligence_engine.planner import AdaptivePlanner, Plan, PlanStep, topological_steps, validate_plan


def test_adaptive_planner_creates_research_reason_verify():
    plan = AdaptivePlanner().build("research the latest dental implant evidence")
    assert [s.id for s in plan.steps] == ["research", "reason", "verify"]


def test_adaptive_planner_gates_consequential_action():
    plan = AdaptivePlanner().build("research and publish a report")
    assert plan.steps[-1].id == "act"
    assert plan.steps[-1].requires_approval


def test_plan_rejects_dependency_cycle():
    plan = Plan("x", (
        PlanStep("a", "a", "a", ("b",)),
        PlanStep("b", "b", "b", ("a",)),
    ))
    try:
        topological_steps(plan)
        assert False, "cycle should fail"
    except ValueError:
        pass


def test_plan_rejects_blocked_capability():
    plan = Plan("x", (PlanStep("a", "danger", "danger"),))
    try:
        validate_plan(plan, blocked_capabilities={"danger"})
        assert False, "blocked capability should fail"
    except PermissionError:
        pass


def test_adaptive_planner_rejects_budget_that_would_drop_verification():
    import pytest

    with pytest.raises(ValueError, match="exceeding configured budget"):
        AdaptivePlanner(max_steps=2).build("research the latest evidence")
