"""Safe, evidence-driven self-improvement loop for the Universal Intelligence Engine.

This module does not permit uncontrolled self-modifying production code.
It treats source-code changes as candidates that must pass gates before promotion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    VERIFIED = "verified"
    PROMOTED = "promoted"


@dataclass(frozen=True)
class ImprovementSignal:
    goal: str
    failure: str | None = None
    evidence: Sequence[Mapping[str, Any]] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeCandidate:
    candidate_id: str
    description: str
    patch: str
    tests: Sequence[str] = ()
    expected_metrics: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    score: float = 0.0
    details: str = ""


@dataclass(frozen=True)
class ImprovementResult:
    candidate_id: str
    status: CandidateStatus
    score: float
    gates: Sequence[GateResult]
    rationale: str


class SelfImprovementEngine:
    """Orchestrates propose -> verify -> compare -> promote.

    The engine is deliberately model/provider agnostic. A caller supplies
    candidate generation and verification functions. Production promotion
    should remain behind an explicit policy/approval boundary.
    """

    def __init__(
        self,
        proposer: Callable[[ImprovementSignal], Sequence[CodeCandidate]],
        verifier: Callable[[CodeCandidate], Sequence[GateResult]],
        *,
        promotion_threshold: float = 0.85,
        minimum_gates: int = 4,
    ) -> None:
        if not 0.0 <= promotion_threshold <= 1.0:
            raise ValueError("promotion_threshold must be between 0 and 1")
        self.proposer = proposer
        self.verifier = verifier
        self.promotion_threshold = promotion_threshold
        self.minimum_gates = minimum_gates

    @staticmethod
    def _score(gates: Sequence[GateResult]) -> float:
        if not gates:
            return 0.0
        # Failed safety/security gates have veto power.
        for gate in gates:
            if gate.name.lower() in {"security", "policy", "regression"} and not gate.passed:
                return 0.0
        return sum(max(0.0, min(1.0, g.score if g.passed else 0.0)) for g in gates) / len(gates)

    def evaluate(self, signal: ImprovementSignal) -> Sequence[ImprovementResult]:
        results: list[ImprovementResult] = []
        for candidate in self.proposer(signal):
            gates = tuple(self.verifier(candidate))
            score = self._score(gates)
            passed_count = sum(g.passed for g in gates)
            verified = (
                len(gates) >= self.minimum_gates
                and passed_count == len(gates)
                and score >= self.promotion_threshold
            )
            status = CandidateStatus.VERIFIED if verified else CandidateStatus.REJECTED
            rationale = (
                "Candidate passed all configured gates and may enter an explicit "
                "promotion workflow."
                if verified
                else "Candidate failed one or more verification gates."
            )
            results.append(
                ImprovementResult(
                    candidate_id=candidate.candidate_id,
                    status=status,
                    score=score,
                    gates=gates,
                    rationale=rationale,
                )
            )
        return tuple(results)


def default_gate_order() -> tuple[str, ...]:
    """Canonical gates for an AI that improves its own software safely."""
    return (
        "syntax",
        "unit_tests",
        "integration",
        "behavioral",
        "evidence",
        "regression",
        "security",
        "policy",
        "performance",
        "reproducibility",
    )
