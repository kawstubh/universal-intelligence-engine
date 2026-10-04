"""Provider-neutral planning primitives."""

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class PlanStep:
    id: str
    capability: str
    objective: str
    requires_approval: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Plan:
    goal: str
    steps: Sequence[PlanStep]
    rationale: str | None = None


def validate_plan(plan: Plan, blocked_capabilities: set[str] | None = None) -> None:
    blocked = blocked_capabilities or set()
    for step in plan.steps:
        if step.capability in blocked:
            raise PermissionError(f"Plan contains blocked capability: {step.capability}")
