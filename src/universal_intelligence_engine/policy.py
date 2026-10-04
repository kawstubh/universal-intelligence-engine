"""Safety and execution policies for domain adapters."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class CapabilityPolicy:
    allowed_tools: frozenset[str] = frozenset()
    require_human_approval_for: frozenset[str] = frozenset()
    blocked_actions: frozenset[str] = frozenset()
    allowed_context_domains: frozenset[str] = frozenset()
    blocked_context_domains: frozenset[str] = frozenset()

    def allows(self, action: str) -> bool:
        return action not in self.blocked_actions

    def needs_approval(self, action: str) -> bool:
        return action in self.require_human_approval_for

    def filter_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Return only explicitly permitted top-level context domains."""
        filtered: dict[str, Any] = {}
        for key, value in context.items():
            if key in self.blocked_context_domains:
                continue
            if self.allowed_context_domains and key not in self.allowed_context_domains:
                continue
            filtered[key] = value
        return filtered


def policy_for(
    allowed: Iterable[str] = (),
    approval: Iterable[str] = (),
    blocked: Iterable[str] = (),
    context: Iterable[str] = (),
    blocked_context: Iterable[str] = (),
) -> CapabilityPolicy:
    return CapabilityPolicy(
        frozenset(allowed),
        frozenset(approval),
        frozenset(blocked),
        frozenset(context),
        frozenset(blocked_context),
    )
