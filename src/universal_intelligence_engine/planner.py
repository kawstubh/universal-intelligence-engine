"""Adaptive, provider-neutral planning for UIE.

The planner converts a natural-language goal into a bounded execution graph.
It is intentionally deterministic and conservative: external models may later
propose richer plans, but UIE keeps validation, dependency ordering, budgets,
and capability policy outside the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PlanStep:
    id: str
    capability: str
    objective: str
    depends_on: tuple[str, ...] = ()
    requires_approval: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    goal: str
    steps: Sequence[PlanStep]
    rationale: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AdaptivePlanner:
    """Build a safe baseline plan from a goal without requiring an LLM."""

    _research_terms = re.compile(
        r"\b(research|find|latest|current|compare|investigate|analy[sz]e|verify|check|study)\b",
        re.I,
    )
    _action_terms = re.compile(
        r"\b(send|create|publish|deploy|delete|modify|buy|book|execute|contact|update)\b",
        re.I,
    )

    def __init__(self, max_steps: int = 8) -> None:
        self.max_steps = max(2, max_steps)

    def build(self, goal: str, context: Mapping[str, Any] | None = None) -> Plan:
        goal = goal.strip()
        if not goal:
            raise ValueError("Goal must not be empty")

        context = context or {}
        steps: list[PlanStep] = []
        research = bool(self._research_terms.search(goal)) or bool(
            context.get("requires_research")
        )

        if research:
            steps.append(
                PlanStep("research", "research", f"Gather evidence needed for: {goal}")
            )

        reason_dep = ("research",) if research else ()
        steps.append(
            PlanStep("reason", "reason", f"Reason about the goal: {goal}", reason_dep)
        )

        verify_dep = ("reason",)
        steps.append(
            PlanStep("verify", "verify", f"Verify the proposed result for: {goal}", verify_dep)
        )

        if self._action_terms.search(goal):
            steps.append(
                PlanStep(
                    "act",
                    "act",
                    f"Execute the requested action only after verification: {goal}",
                    ("verify",),
                    requires_approval=True,
                )
            )

        if len(steps) > self.max_steps:
            raise ValueError(
                f"Plan requires {len(steps)} steps, exceeding configured budget of {self.max_steps}"
            )

        return Plan(
            goal=goal,
            steps=tuple(steps),
            rationale="Research when needed, reason, verify, then perform consequential actions.",
            metadata={"planner": "adaptive-native", "version": "0.1.0"},
        )


def validate_plan(
    plan: Plan,
    blocked_capabilities: set[str] | None = None,
    allowed_capabilities: set[str] | None = None,
) -> None:
    blocked = blocked_capabilities or set()
    allowed = allowed_capabilities

    ids: set[str] = set()
    for step in plan.steps:
        if step.id in ids:
            raise ValueError(f"Duplicate plan step: {step.id}")
        ids.add(step.id)
        if step.capability in blocked:
            raise PermissionError(f"Plan contains blocked capability: {step.capability}")
        if allowed is not None and step.capability not in allowed:
            raise PermissionError(f"Plan capability not allowlisted: {step.capability}")
        for dep in step.depends_on:
            if dep not in ids:
                raise ValueError(f"Step {step.id} depends on unknown or later step: {dep}")


def topological_steps(plan: Plan) -> tuple[PlanStep, ...]:
    """Return dependency-safe order and reject cycles."""
    validate_plan(plan)
    remaining = {step.id: step for step in plan.steps}
    done: set[str] = set()
    ordered: list[PlanStep] = []

    while remaining:
        ready = [
            step for step in remaining.values()
            if all(dep in done for dep in step.depends_on)
        ]
        if not ready:
            raise ValueError("Plan contains a dependency cycle")
        for step in ready:
            ordered.append(step)
            done.add(step.id)
            remaining.pop(step.id)

    return tuple(ordered)
