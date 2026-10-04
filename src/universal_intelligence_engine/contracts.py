"""Core contracts for the Universal Intelligence Engine."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Evidence:
    source: str
    title: str
    retrieved_at: str
    content: str
    url: str | None = None
    published_at: str | None = None
    language: str | None = None
    locale: str | None = None
    confidence: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntelligenceRequest:
    goal: str
    context: Mapping[str, Any] = field(default_factory=dict)
    locale: str | None = None
    language: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntelligenceResponse:
    answer: Any
    evidence: Sequence[Evidence] = ()
    actions: Sequence[Mapping[str, Any]] = ()
    confidence: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class KnowledgeProvider(Protocol):
    def search(self, request: IntelligenceRequest) -> Sequence[Evidence]:
        """Retrieve evidence relevant to a request."""


class ReasoningProvider(Protocol):
    def reason(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
    ) -> IntelligenceResponse:
        """Reason over request context and evidence."""


class Evaluator(Protocol):
    def evaluate(self, response: IntelligenceResponse) -> Mapping[str, Any]:
        """Evaluate quality, completeness, and policy compliance."""


class LearningStore(Protocol):
    def record(self, event: Mapping[str, Any]) -> None:
        """Persist a controlled learning/feedback event."""
