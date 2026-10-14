"""Automated offline smoke test for the operator-facing UIE demo."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = REPO_ROOT / "scripts" / "demo_uie_live.py"


def test_live_demo_executes_cognitive_cycle_and_verifies_integrity() -> None:
    """Run the real demo entry point without network access or interactive input."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    completed = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT), "--no-pause"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    output = completed.stdout + "\n" + completed.stderr
    assert completed.returncode == 0, output
    for expected in (
        "[1/5] Query processing & tripartite memory bootstrap",
        "[2/5] PRM trajectory deliberation",
        "[3/5] Tripartite recall after query",
        "[4/5] Epistemic void detection & bounded dispatch",
        "Mission enqueued                True",
        "[5/5] Cryptographic provenance verification",
        "SHA-256 ledger continuity       VERIFIED",
        "Independent ledger check        VERIFIED",
        "DEMO COMPLETE: all five layers executed; integrity checks passed.",
        "No external URLs were fetched. The queued mission was not harvested.",
    ):
        assert expected in output, f"Expected demo output missing: {expected!r}\n{output}"
