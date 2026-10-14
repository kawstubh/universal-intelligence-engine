"""Causal projections and MCP JSON-RPC protocol tests."""
import json
import sqlite3
import socket

import pytest

from universal_intelligence_engine.learning.causal_engine import CausalWorldModel
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.contracts import Evidence
from universal_intelligence_engine.reasoning.deliberation import ProcessRewardEvaluator
from universal_intelligence_engine.security.provenance_crypto import verify_integrity_ledger
from universal_intelligence_engine.tools.mcp_client import MCPClient, MCPError


def _edge(store, subject, predicate, obj, confidence=0.9):
    return store.ingest_claim({"subject": subject, "predicate": predicate, "object_claim": obj,
                               "confidence": confidence, "domain": "test", "provenance_url": "test://fixture"})


def test_causal_cascade_projects_critical_counterfactual_risk(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    _edge(store, "Drug X", "INHIBITS", "Enzyme Y")
    _edge(store, "Enzyme Y", "TRIGGERS", "Condition Z")
    result = CausalWorldModel(store).simulate_intervention("Drug X", "inhibit Drug X", max_depth=2)
    assert result.traversed_edges >= 2
    assert result.critical_contraindications
    assert any("Condition Z" == item["entity"] for item in result.critical_contraindications)
    assert result.safety_score <= 0.2


def test_causal_prevents_and_contraindications_are_structured(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    _edge(store, "Shield A", "PREVENTS", "Hazard B")
    _edge(store, "Hazard B", "CONTRAINDICATES", "Action C")
    result = CausalWorldModel(store).simulate_intervention("Shield A", "activate shield", 2)
    assert result.projected_benefits
    assert result.critical_contraindications


def test_mcp_lifecycle_tool_call_and_integrity_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    ledger = tmp_path / "mcp.sqlite3"
    seen = []

    def transport(endpoint, body, timeout):
        request = json.loads(body)
        seen.append(request)
        if request.get("method") == "notifications/initialized":
            return b""
        method = request["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
        else:
            raise AssertionError(method)
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()

    client = MCPClient("https://mcp.example.test/rpc", str(ledger), transport=transport)
    with pytest.raises(MCPError):
        client.list_tools()
    client.initialize()
    assert client.list_tools()[0]["name"] == "echo"
    result = client.call_tool("echo", {"value": "hello"})
    assert result.result["content"][0]["text"] == "ok"
    assert result.event_sequence == 1
    with sqlite3.connect(ledger) as conn:
        conn.row_factory = sqlite3.Row
        ok, error = verify_integrity_ledger(conn)
        assert ok, error
        row = conn.execute("SELECT * FROM uie_integrity_ledger").fetchone()
        assert row["entity_type"] == "mcp_tool_invocation"
        assert row["event_type"] == "OUTBOUND"


@pytest.mark.parametrize("endpoint", [
    "http://mcp.example.test/rpc",
    "https://127.0.0.1/rpc",
    "https://user:password@mcp.example.test/rpc",
])
def test_mcp_rejects_unsafe_endpoint(endpoint, tmp_path):
    with pytest.raises(ValueError):
        MCPClient(endpoint, str(tmp_path / "ledger.sqlite3"), transport=lambda *_: b"{}")


def test_mcp_enforces_initialization_and_json_rpc_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    def bad_transport(endpoint, body, timeout):
        request = json.loads(body)
        if request.get("method") == "notifications/initialized":
            return b""
        if request.get("method") == "initialize":
            return json.dumps({"jsonrpc": "2.0", "id": request["id"],
                               "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}).encode()
        return json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode()
    client = MCPClient("https://mcp.example.test/rpc", str(tmp_path / "ledger.sqlite3"), transport=bad_transport)
    client.initialize()
    with pytest.raises(MCPError, match="invalid JSON-RPC envelope"):
        client.list_tools()


def test_prm_penalizes_intervention_with_causal_cascade(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    _edge(store, "Drug X", "INHIBITS", "Enzyme Y")
    _edge(store, "Enzyme Y", "TRIGGERS", "Condition Z")
    evaluator = ProcessRewardEvaluator(causal_engine=CausalWorldModel(store))
    evidence = Evidence(source="fixture", title="Candidate intervention", retrieved_at="2026-10-15",
                        content="Candidate intervention on Drug X", confidence=0.9,
                        metadata={"intervention": {"target_entity": "Drug X",
                                                   "intervention_action": "inhibit Drug X", "max_depth": 2}})
    score, signals = evaluator.score_step("Candidate intervention", evidence, set())
    assert score < 0.5
    assert signals["domain_constraint_adherence"] == 0.0
    assert signals["causal_critical_contraindications"]
