"""Bounded autonomous execution loop for UIE."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

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
    """Execute an explicit, bounded action plan with policy and verification.

    The planner remains replaceable. The runtime owns safety, step limits,
    observation, failure capture, and final verification.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        max_steps: int = 8,
        verifier: Callable[[str, Any], bool] | None = None,
    ) -> None:
        self.tools = tools
        self.max_steps = max(1, max_steps)
        self.verifier = verifier

    def run(self, goal: str, plan: list[Mapping[str, Any]]) -> AgentRun:
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
            return AgentRun(
                goal, "failed", tuple(steps), output=output,
                metadata={"reason": "step_limit_exceeded", "max_steps": self.max_steps},
            )

        verified = self.verifier(goal, output) if self.verifier else True
        status = "verified" if verified else "unverified"
        return AgentRun(
            goal, status, tuple(steps), output=output,
            metadata={"step_count": len(steps), "verified": verified},
        )
