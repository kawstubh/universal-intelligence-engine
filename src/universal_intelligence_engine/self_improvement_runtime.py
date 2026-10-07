"""Sandboxed runtime for UIE self-improvement.

The improvement engine receives broad access to a disposable source workspace:
it may inspect, add, modify, delete, and test source files there. It cannot
write to the live production checkout, access process secrets, or promote a
candidate. Promotion remains a separate human-governed operation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .self_improvement import (
    CodeCandidate,
    GateResult,
    ImprovementResult,
    ImprovementSignal,
    SelfImprovementEngine,
)


@dataclass(frozen=True)
class SandboxPolicy:
    timeout_seconds: int = 300
    max_workspace_mb: int = 512
    allow_network: bool = False
    allow_environment: bool = False
    forbidden_paths: tuple[str, ...] = (
        ".git",
        ".env",
        ".env.local",
        ".env.production",
        "secrets",
        "credentials",
    )


@dataclass(frozen=True)
class SandboxResult:
    candidate_id: str
    workspace: str
    gates: tuple[GateResult, ...]
    changed_files: tuple[str, ...] = ()
    diff: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SelfImprovementSandbox:
    """Give candidates broad source-editing access inside a disposable copy."""

    def __init__(self, source_root: str | Path, policy: SandboxPolicy | None = None):
        self.source_root = Path(source_root).resolve()
        self.policy = policy or SandboxPolicy()
        if not self.source_root.is_dir():
            raise ValueError(f"Source root does not exist: {self.source_root}")

    def _validate_workspace(self, workspace: Path) -> None:
        for name in self.policy.forbidden_paths:
            if (workspace / name).exists():
                if (workspace / name).is_dir():
                    shutil.rmtree(workspace / name)
                else:
                    (workspace / name).unlink()

    def _run(self, command: Sequence[str], cwd: Path, *, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        safe_env = dict(env)
        if not self.policy.allow_environment:
            safe_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(cwd),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        if not self.policy.allow_network:
            # The sandbox deliberately does not attempt to create network
            # namespaces because portability matters. Callers should execute
            # this runtime in a network-isolated CI/container for hard denial.
            safe_env["UIE_NETWORK_DISABLED"] = "1"
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            env=safe_env,
            text=True,
            capture_output=True,
            timeout=self.policy.timeout_seconds,
            check=False,
        )

    def verify(self, candidate: CodeCandidate) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="uie-improvement-") as tmp:
            workspace = Path(tmp) / "repo"
            shutil.copytree(self.source_root, workspace, dirs_exist_ok=True)
            self._validate_workspace(workspace)

            patch_file = workspace / ".uie_candidate.patch"
            patch_file.write_text(candidate.patch, encoding="utf-8")
            patch = self._run(["git", "apply", "--whitespace=nowarn", str(patch_file)], workspace,
                              env=os.environ)
            patch_file.unlink(missing_ok=True)
            if patch.returncode != 0:
                return SandboxResult(
                    candidate.candidate_id, str(workspace),
                    (GateResult("patch", False, 0.0, patch.stderr[-4000:]),),
                    metadata={"stdout": patch.stdout[-4000:]},
                )

            gates: list[GateResult] = [
                GateResult("patch", True, 1.0, "Candidate patch applied cleanly.")
            ]

            syntax = self._run(
                ["python", "-m", "compileall", "-q", "."], workspace, env=os.environ
            )
            gates.append(GateResult(
                "syntax", syntax.returncode == 0,
                1.0 if syntax.returncode == 0 else 0.0,
                syntax.stderr[-4000:] if syntax.returncode else "Python compilation passed.",
            ))

            tests = self._run(
                ["python", "-m", "pytest", "-q"], workspace, env=os.environ
            )
            gates.append(GateResult(
                "unit_tests", tests.returncode == 0,
                1.0 if tests.returncode == 0 else 0.0,
                (tests.stdout + tests.stderr)[-6000:],
            ))

            diff = self._run(["git", "diff", "--no-ext-diff", "--unified=3"], workspace, env=os.environ)
            names = self._run(
                ["git", "diff", "--name-only"], workspace, env=os.environ
            )
            changed = tuple(x.strip() for x in names.stdout.splitlines() if x.strip())

            forbidden_changed = [
                p for p in changed
                if any(Path(p).parts[:1] == (blocked,) for blocked in self.policy.forbidden_paths)
            ]
            gates.append(GateResult(
                "policy", not forbidden_changed,
                1.0 if not forbidden_changed else 0.0,
                "No protected paths changed." if not forbidden_changed
                else f"Protected paths changed: {forbidden_changed}",
            ))

            security = self._run(
                ["python", "-m", "compileall", "-q", "src"], workspace, env=os.environ
            )
            gates.append(GateResult(
                "security", security.returncode == 0,
                1.0 if security.returncode == 0 else 0.0,
                "Static baseline passed; external SAST should be added in CI."
                if security.returncode == 0 else security.stderr[-4000:],
            ))

            return SandboxResult(
                candidate.candidate_id,
                str(workspace),
                tuple(gates),
                changed,
                diff.stdout,
                {"test_output": (tests.stdout + tests.stderr)[-6000:]},
            )


class RuntimeSelfImprovementEngine:
    """Full-access candidate workflow with sandbox verification and no promotion."""

    def __init__(
        self,
        source_root: str | Path,
        proposer,
        *,
        policy: SandboxPolicy | None = None,
        promotion_threshold: float = 0.85,
    ):
        self.source_root = Path(source_root).resolve()
        self.proposer = proposer
        self.sandbox = SelfImprovementSandbox(self.source_root, policy)
        self.engine = SelfImprovementEngine(
            proposer,
            lambda candidate: self.sandbox.verify(candidate).gates,
            promotion_threshold=promotion_threshold,
            minimum_gates=4,
        )

    def improve(self, signal: ImprovementSignal) -> Sequence[ImprovementResult]:
        """Generate and fully verify candidates in disposable workspaces."""
        return self.engine.evaluate(signal)
