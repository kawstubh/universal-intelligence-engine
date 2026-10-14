import asyncio
import sqlite3

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.db.storage_engine import StorageEngine, _pg_sql
from universal_intelligence_engine.learning.cognitive_memory import CognitiveMemoryEngine
from universal_intelligence_engine.learning.deep_reflexion import DeepReflexionEngine
from universal_intelligence_engine.reasoning import UIEReasoningCoordinator


def test_initialize_schema_upgrades_legacy_v1_episode_table_without_data_loss(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE uie_episodes (id TEXT PRIMARY KEY, goal TEXT NOT NULL, trace_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO uie_episodes(id, goal, trace_json, created_at) VALUES(?,?,?,?)",
                     ("old-1", "legacy goal", "{\\\"source\\\":\\\"v1\\\"}", "2025-01-01T00:00:00Z"))
    async def run():
        engine = StorageEngine(sqlite_path=str(path))
        await engine.connect()
        try:
            await engine.initialize_schema()
            await engine.initialize_schema()
            row = await engine.fetchone("SELECT * FROM uie_episodes WHERE id=?", ("old-1",))
            assert row["goal"] == "legacy goal"
            assert row["trace_json"] == "{\\\"source\\\":\\\"v1\\\"}"
            assert row["trajectory_scores_json"] == "[]"
            assert row["feedback_json"] == "{}"
            assert row["domain"] is None
            assert row["payload_json"] is None
        finally:
            await engine.close()
    asyncio.run(run())


def test_positional_bindings_are_transparent_across_sqlite_and_postgres(tmp_path):
    sql = "SELECT id FROM uie_entities WHERE normalized_name=? AND domain=? AND name='literal ?'"
    assert sql.count("?") == 3
    assert _pg_sql(sql) == "SELECT id FROM uie_entities WHERE normalized_name=$1 AND domain=$2 AND name='literal ?'"
    async def run():
        engine = StorageEngine(sqlite_path=str(tmp_path / "bindings.sqlite3"))
        await engine.connect()
        try:
            await engine.initialize_schema()
            await engine.execute("INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?)",
                                 ("x", "Literal ?", "entity", "test", "now", "literal"))
            row = await engine.fetchone("SELECT id FROM uie_entities WHERE normalized_name=? AND domain=?", ("literal", "test"))
            assert row["id"] == "x"
        finally:
            await engine.close()
    asyncio.run(run())


def test_async_coordinator_runs_graph_scoring_and_episode_ledger_through_storage(tmp_path):
    class Provider:
        def reason(self, request, evidence):
            return IntelligenceResponse(answer="simulated async result", evidence=tuple(evidence), confidence=.8, metadata={"provider":"test"})
    async def run():
        path = tmp_path / "deliberation.sqlite3"
        engine = StorageEngine(sqlite_path=str(path))
        await engine.connect()
        try:
            await engine.initialize_schema()
            memory = CognitiveMemoryEngine(path)
            coordinator = UIEReasoningCoordinator(deep_reflexion=DeepReflexionEngine(memory=memory))
            coordinator.reflexion.applicable_rules = lambda domain=None: []
            coordinator.deep_reflexion.active_heuristics = lambda domain=None: []
            evidence = [
                Evidence(source="a", title="Battery life", retrieved_at="2026-10-14T00:00:00+00:00", content="Device A battery lasts 12 hours", confidence=.9, url="https://example.test/a"),
                Evidence(source="b", title="Processor performance", retrieved_at="2026-10-14T00:00:00+00:00", content="Device B processor benchmark is strong", confidence=.88, url="https://example.test/b"),
            ]
            # The sync graph and episode methods are forbidden on this path.
            memory.graph.get_entity_subgraph = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync graph path used"))
            memory.record_episode = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync episode path used"))
            result = await coordinator.areason(
                IntelligenceRequest(goal="compare battery life and processor performance", context={"domain":"test"}),
                evidence, Provider(), storage=engine)
            assert result.answer == "simulated async result"
            assert result.metadata["deliberation"]["trajectory_count"] > 0
            assert result.metadata["episode_id"]
            saved = await engine.fetchone("SELECT id,goal FROM uie_episodes WHERE id=?", (result.metadata["episode_id"],))
            ledger = await engine.fetchone("SELECT entity_id,record_hash FROM uie_integrity_ledger WHERE entity_id=?", (result.metadata["episode_id"],))
            assert saved["goal"].startswith("compare battery")
            assert ledger["record_hash"]
        finally:
            await engine.close()
    asyncio.run(run())
