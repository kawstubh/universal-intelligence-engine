from universal_intelligence_engine.self_improvement import (
    CandidateStatus,
    CodeCandidate,
    GateResult,
    ImprovementSignal,
    SelfImprovementEngine,
    default_gate_order,
)


def test_self_improvement_rejects_security_failure():
    engine = SelfImprovementEngine(
        lambda _: [
            CodeCandidate("a", "candidate", "patch")
        ],
        lambda _: [
            GateResult("unit_tests", True, 1.0),
            GateResult("security", False, 0.0),
            GateResult("regression", True, 1.0),
            GateResult("policy", True, 1.0),
        ],
    )
    result = engine.evaluate(ImprovementSignal(goal="improve retrieval"))[0]
    assert result.status == CandidateStatus.REJECTED
    assert result.score == 0.0


def test_self_improvement_verifies_strong_candidate():
    engine = SelfImprovementEngine(
        lambda _: [CodeCandidate("b", "candidate", "patch")],
        lambda _: [
            GateResult("unit_tests", True, 1.0),
            GateResult("integration", True, 0.95),
            GateResult("regression", True, 1.0),
            GateResult("security", True, 1.0),
            GateResult("policy", True, 1.0),
        ],
    )
    result = engine.evaluate(ImprovementSignal(goal="improve reasoning"))[0]
    assert result.status == CandidateStatus.VERIFIED
    assert result.score >= 0.85


def test_gate_order_is_explicit():
    gates = default_gate_order()
    assert gates.index("security") < gates.index("performance")
    assert "reproducibility" in gates
