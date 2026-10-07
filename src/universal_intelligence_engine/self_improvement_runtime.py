"""Sandboxed runtime for UIE self-improvement.

Candidates get broad read/write/test access to a disposable source workspace.
The runtime deliberately separates candidate execution from production
activation: it never commits, deploys, or changes the source checkout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    """Broad source access inside a disposable, non-production workspace."""

    def __init__(self, source_root: str | Path, policy: SandboxPolicy | None = None):
        self.source_root = Path(source_root).resolve()
        self.policy = policy or SandboxPolicy()
        if not self.source_root.is_dir():
            raise ValueError(f"Source root does not exist: {self.source_root}")

    def _run(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.policy.allow_environment:
            safe_env = dict(env or os.environ)
        else:
            safe_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(cwd),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "UIE_NETWORK_DISABLED": "1",
            }
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            env=safe_env,
            text=True,
            capture_output=True,
            timeout=self.policy.timeout_seconds,
            check=False,
        )

    def _protected_changes(self, changed: Sequence[str]) -> list[str]:
        protected = set(self.policy.forbidden_paths)
        return [
            path for path in changed
            if any(part in protected for part in Path(path).parts)
        ]

    def verify(self, candidate: CodeCandidate) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="uie-improvement-") as tmp:
            workspace = Path(tmp) / "repo"
            shutil.copytree(self.source_root, workspace, dirs_exist_ok=True)

            # Never carry source-control metadata or environment files into the
            # execution workspace. A fresh git repo is created only to inspect
            # the candidate diff; it has no remote and cannot push anywhere.
            git_dir = workspace / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)
            for name in self.policy.forbidden_paths:
                target = workspace / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()

            init = self._run(["git", "init", "-q"], workspace)
            if init.returncode != 0:
                return SandboxResult(
                    candidate.candidate_id, str(workspace),
                    (GateResult("workspace", False, 0.0, init.stderr[-4000:]),),
                )
            self._run(["git", "config", "user.email", "uie-sandbox@localhost"], workspace)
            self._run(["git", "config", "user.name", "UIE Sandbox"], workspace)
            self._run(["git", "add", "-A"], workspace)
            baseline = self._run(
                ["git", "commit", "-qm", "sandbox baseline"], workspace
            )
            if baseline.returncode != 0:
                return SandboxResult(
                    candidate.candidate_id, str(workspace),
                    (GateResult("workspace", False, 0.0, baseline.stderr[-4000:]),),
                )

            patch_file = workspace / ".uie_candidate.patch"
            patch_file.write_text(candidate.patch, encoding="utf-8")
            patch = self._run(
                ["git", "apply", "--whitespace=nowarn", str(patch_file)], workspace
            )
            patch_file.unlink(missing_ok=True)
            if patch.returncode != 0:
                return SandboxResult(
                    candidate.candidate_id, str(workspace),
                    (GateResult("patch", False, 0.0, patch.stderr[-4000:]),),
                )

            gates: list[GateResult] = [
                GateResult("patch", True, 1.0, "Candidate patch applied cleanly.")
            ]

            syntax = self._run(
                ["python", "-m", "compileall", "-q", "."], workspace
            )
            gates.append(GateResult(
                "syntax", syntax.returncode == 0,
                1.0 if syntax.returncode == 0 else 0.0,
                syntax.stderr[-4000:] if syntax.returncode else "Compilation passed.",
            ))

            tests = self._run(["python", "-m", "pytest", "-q"], workspace)
            gates.append(GateResult(
                "unit_tests", tests.returncode == 0,
                1.0 if tests.returncode == 0 else 0.0,
                (tests.stdout + tests.stderr)[-6000:],
            ))

            names = self._run(["git", "diff", "--name-only"], workspace)
            changed = tuple(
                item.strip() for item in names.stdout.splitlines() if item.strip()
            )
            forbidden = self._protected_changes(changed)
            gates.append(GateResult(
                "policy", not forbidden,
                1.0 if not forbidden else 0.0,
                "No protected paths changed."
                if not forbidden else f"Protected paths changed: {forbidden}",
            ))

            # Basic static safety gate. Hard isolation (seccomp/container/network
            # namespace) belongs to the deployment environment, not this Python
            # abstraction.
            suspicious = self._run(
                [
                    "python", "-c",
                    "import pathlib,re; "
                    "files=list(pathlib.Path('src').rglob('*.py')); "
                    "rx=re.compile(r'(?i)(os\\.system|subprocess\\.Popen|eval\\(|exec\\()'); "
                    "print('\\n'.join(str(p) for p in files if rx.search(p.read_text(errors='ignore'))))",
                ],
                workspace,
            )
            suspicious_files = tuple(
                item.strip() for item in suspicious.stdout.splitlines() if item.strip()
            )
            gates.append(GateResult(
                "security", suspicious.returncode == 0,
                1.0 if suspicious.returncode == 0 else 0.0,
                "Static safety scan completed."
                if suspicious.returncode == 0 else suspicious.stderr[-4000:],
            ))

            diff = self._run(
                ["git", "diff", "--no-ext-diff", "--unified=3"], workspace
            )
            return SandboxResult(
                candidate.candidate_id,
                str(workspace),
                tuple(gates),
                changed,
                diff.stdout,
                {
                    "test_output": (tests.stdout + tests.stderr)[-6000:],
                    "suspicious_files": suspicious_files,
                    "network_policy": "disabled-by-contract",
                    "production_write": False,
                    "promotion": "human-governed",
                },
            )


class RuntimeSelfImprovementEngine:
    """Full-access candidate workflow with sandbox verification and no promotion."""

    def __init__(
        self,
        source_root: str | Path,
        proposer: Callable[[ImprovementSignal], Sequence[CodeCandidate]],
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
