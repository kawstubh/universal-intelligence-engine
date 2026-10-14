import json
import sqlite3

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.reasoning.tool_reflexion import ToolReflexionEngine
from universal_intelligence_engine.reasoning.deliberation import UIEReasoningCoordinator
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger


def test_failed_tool_call_extracts_constraint_and_appends_integrity_event(tmp_path):
    db = tmp_path / "reflexion.sqlite3"
    memory = HeuristicMemory(db)
    reflexion = ToolReflexionEngine(memory)

    def bad_query():
        raise ValueError("invalid query syntax")

    try:
        reflexion.execute("europe-pmc", "dental disease and oral health", bad_query)
    except ValueError:
        pass
    else:
        raise AssertionError("failed tool call must preserve the original exception")

    rules = memory.get_rules("europe-pmc")
    assert len(rules) == 1
    assert "uppercase AND, OR, and NOT" in rules[0]["rule_text"]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT entity_type,event_type,payload_canonical_json FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row[0] == "heuristic"
        assert row[1] == "INSERT"
        assert "uppercase_boolean_operators" in row[2]
        assert verify_integrity_ledger(conn)[0] is True


def test_subsequent_query_reuses_constraint_and_coordinator_injects_it(tmp_path):
    memory = HeuristicMemory(tmp_path / "reflexion.sqlite3")
    reflexion = ToolReflexionEngine(memory)
    original = "dental disease and oral health"
    reflexion.reflect_failure("europe-pmc", original, "ValueError", "bad query syntax")
    prepared = reflexion.prepare_query("europe-pmc", original)
    assert prepared == "dental disease AND oral health"

    class Provider:
        seen_context = None
        def reason(self, request, evidence):
            self.seen_context = dict(request.context)
            return IntelligenceResponse(answer="ok", evidence=tuple(evidence), confidence=.8, metadata={})

    provider = Provider()
    coordinator = UIEReasoningCoordinator(reflexion=reflexion)
    coordinator.reason(IntelligenceRequest(goal=original, context={"domain": "europe-pmc"}), [], provider)
    injected = provider.seen_context["uie_procedural_heuristics"]
    assert any("uppercase AND, OR, and NOT" in rule["rule_text"] for rule in injected)


def test_empty_tool_payload_is_reflected_without_failing_successful_call(tmp_path):
    memory = HeuristicMemory(tmp_path / "reflexion.sqlite3")
    reflexion = ToolReflexionEngine(memory)
    result = reflexion.execute("wikipedia", "query with no matches", lambda: {"query": {"search": []}},
                               is_empty=lambda payload: not payload["query"]["search"])
    assert result == {"query": {"search": []}}
    rules = memory.get_rules("wikipedia")
    assert len(rules) == 1
    assert rules[0]["rule_type"] == "tool_failure_constraint"

