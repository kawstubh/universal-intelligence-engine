import pytest

from universal_intelligence_engine.policy import policy_for
from universal_intelligence_engine.tools import ToolRegistry, ToolSpec


def test_tool_registry_requires_allowlist():
    registry = ToolRegistry(policy_for(allowed=["echo"]))
    registry.register(ToolSpec("echo", "Echo text", lambda text: text))
    assert registry.call("echo", text="ok") == "ok"


def test_tool_registry_blocks_unapproved_tool():
    registry = ToolRegistry(policy_for())
    registry.register(ToolSpec("echo", "Echo text", lambda text: text))
    with pytest.raises(PermissionError):
        registry.call("echo", text="blocked")
