"""Safety and execution policy primitives for domain adapters."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CapabilityPolicy:
    allowed_tools: frozenset[str] = frozenset()
    require_human_approval_for: frozenset[str] = frozenset()
    blocked_actions: frozenset[str] = frozenset()

    def allows(self, action: str) -> bool:
        return action not in self.blocked_actions

    def needs_approval(self, action: str) -> bool:
        return action in self.require_human_approval_for


def policy_for(allowed: Iterable[str] = (), approval: Iterable[str] = (), blocked: Iterable[str] = ()) -> CapabilityPolicy:
    return CapabilityPolicy(frozenset(allowed), frozenset(approval), frozenset(blocked))
