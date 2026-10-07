from pathlib import Path

from universal_intelligence_engine.self_improvement import (
    CodeCandidate,
    ImprovementSignal,
)
from universal_intelligence_engine.self_improvement_runtime import (
    RuntimeSelfImprovementEngine,
    SelfImprovementSandbox,
)


def test_sandbox_allows_source_patch_but_never_promotes(tmp_path):
    (tmp_path / "hello.py").write_text("VALUE = 1\n", encoding="utf-8")
    patch = """diff --git a/hello.py b/hello.py
index 8d5a4d0..f8f4f0e 100644
--- a/hello.py
+++ b/hello.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
    # The copied workspace is disposable; the source checkout is untouched.
    result = SelfImprovementSandbox(tmp_path).verify(
        CodeCandidate("candidate-1", "change value", patch)
    )
    assert result.candidate_id == "candidate-1"
    assert not (tmp_path / ".uie_candidate.patch").exists()
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_runtime_rejects_bad_patch(tmp_path):
    (tmp_path / "hello.py").write_text("VALUE = 1\n", encoding="utf-8")

    runtime = RuntimeSelfImprovementEngine(
        tmp_path,
        lambda _: [
            CodeCandidate("bad", "bad patch", "not a git patch")
        ],
    )
    result = runtime.improve(ImprovementSignal(goal="improve"))
    assert result[0].status.value == "rejected"
    assert result[0].score == 0.0
