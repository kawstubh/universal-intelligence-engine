"""Capability-gated tool registry."""

from dataclasses import dataclass
from typing import Any, Callable

from .policy import CapabilityPolicy


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]


class ToolRegistry:
    def __init__(self, policy: CapabilityPolicy | None = None):
        self.policy = policy or CapabilityPolicy()
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
            if tool.name in self.policy.allowed_tools
        ]

    def call(self, name: str, **kwargs: Any) -> Any:
        if not self.policy.allows(name):
            raise PermissionError(f"Tool blocked by policy: {name}")
        if name not in self.policy.allowed_tools:
            raise PermissionError(f"Tool not allowlisted: {name}")
        if self.policy.needs_approval(name):
            raise PermissionError(f"Human approval required before calling: {name}")
        return self._tools[name].handler(**kwargs)
