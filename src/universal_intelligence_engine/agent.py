"""Bounded autonomous execution loop for UIE."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .planner import AdaptivePlanner, Plan, PlanStep, topological_steps
from .policy import CapabilityPolicy
from .tools import ToolRegistry


@dataclass(frozen=True)
class AgentStep:
    index: int
    action: str
    status: str
    result: Any = None
    error: str | None = None


@dataclass(frozen=True)
class AgentRun:
    goal: str
    status: str
    steps: tuple[AgentStep, ...]
    output: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AutonomousAgent:
    """Plan, execute, verify, and conservatively recover from tool failures."""

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        max_steps: int = 8,
        verifier: Callable[[str, Any], bool] | None = None,
        planner: AdaptivePlanner | None = None,
    ) -> None:
        self.tools = tools
        self.max_steps = max(1, max_steps)
        self.verifier = verifier
        self.planner = planner or AdaptivePlanner(max_steps=max_steps)

    def _execute_plan(self, goal: str, plan: Plan) -> AgentRun:
        ordered = topological_steps(plan)
        if len(ordered) > self.max_steps:
            return AgentRun(
                goal, "failed", (), metadata={"reason": "step_limit_exceeded", "max_steps": self.max_steps}
            )

        steps: list[AgentStep] = []
        outputs: dict[str, Any] = {}
        for index, step in enumerate(ordered, start=1):
            args = {"goal": goal, "objective": step.objective, "previous": outputs}
            try:
                result = self.tools.call(step.capability, **args)
                outputs[step.id] = result
                steps.append(AgentStep(index, step.capability, "completed", result))
            except Exception as exc:
                steps.append(AgentStep(index, step.capability, "failed", error=str(exc)))
                return AgentRun(
                    goal, "failed", tuple(steps), output=outputs.get("reason"),
                    metadata={"reason": "tool_failure", "failed_step": step.id},
                )

        output = outputs.get("act", outputs.get("verify", outputs.get("reason")))
        verified = self.verifier(goal, output) if self.verifier else True
        return AgentRun(
            goal,
            "verified" if verified else "unverified",
            tuple(steps),
            output=output,
            metadata={"step_count": len(steps), "verified": verified, "plan": [s.id for s in ordered]},
        )

    def run(self, goal: str, plan: list[Mapping[str, Any]]) -> AgentRun:
        """Backward-compatible execution of an explicit tool plan."""
        steps: list[AgentStep] = []
        output: Any = None
        for index, action in enumerate(plan[: self.max_steps], start=1):
            name = str(action.get("tool", "")).strip()
            args = action.get("args") or {}
            if not name:
                steps.append(AgentStep(index, "invalid", "failed", error="Missing tool name"))
                return AgentRun(goal, "failed", tuple(steps), metadata={"reason": "invalid_plan"})
            try:
                output = self.tools.call(name, **dict(args))
                steps.append(AgentStep(index, name, "completed", output))
            except Exception as exc:
                steps.append(AgentStep(index, name, "failed", error=str(exc)))
                return AgentRun(goal, "failed", tuple(steps), output=output)
        if len(plan) > self.max_steps:
            return AgentRun(goal, "failed", tuple(steps), output=output,
                            metadata={"reason": "step_limit_exceeded", "max_steps": self.max_steps})
        verified = self.verifier(goal, output) if self.verifier else True
        return AgentRun(goal, "verified" if verified else "unverified", tuple(steps), output=output,
                        metadata={"step_count": len(steps), "verified": verified})

    def run_goal(self, goal: str, context: Mapping[str, Any] | None = None) -> AgentRun:
        plan = self.planner.build(goal, context)
        return self._execute_plan(goal, plan)
