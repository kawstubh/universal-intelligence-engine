import asyncio
import hashlib
import json

from universal_intelligence_engine.contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.cognitive_memory import CognitiveMemoryEngine
from universal_intelligence_engine.learning.deep_reflexion import DeepReflexionEngine
from universal_intelligence_engine.reasoning import UIEReasoningCoordinator
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH


def test_async_pipeline_uses_storage_for_graph_episodes_contradictions_and_ledger(tmp_path):
    class Provider:
        def reason(self, request, evidence):
            assert request.context.get("uie_graph_context") is not None
            assert request.context.get("uie_episode_context") is not None
            return IntelligenceResponse(answer="parity result", evidence=tuple(evidence), confidence=.85, metadata={})

    async def run():
        engine = StorageEngine(sqlite_path=str(tmp_path / "pipeline.sqlite3"))
        await engine.connect()
        await engine.initialize_schema()
        memory = CognitiveMemoryEngine(tmp_path / "pipeline.sqlite3")
        graph = memory.graph
        try:
            await graph.aingest_claim({"id":"c1", "subject":"Alpha", "predicate":"has_value", "object_claim":"10", "confidence":.8, "provenance_url":"https://example.test/1"}, storage=engine)
            await graph.aingest_claim({"id":"c2", "subject":"Alpha", "predicate":"has_value", "object_claim":"20", "confidence":.7, "provenance_url":"https://example.test/2"}, storage=engine)
            # Forbid legacy sync DB methods on the async path.
            graph.get_entity_subgraph = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync graph lookup used"))
            graph.ingest_claim = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync graph ingest used"))
            memory.recall_episodes = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync episode retrieval used"))
            memory.record_episode = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync episode write used"))
            coordinator = UIEReasoningCoordinator(deep_reflexion=DeepReflexionEngine(memory=memory))
            coordinator.reflexion.applicable_rules = lambda domain=None: []
            coordinator.deep_reflexion.active_heuristics = lambda domain=None: []
            evidence = [
                Evidence(source="one", title="Alpha value", retrieved_at="2026-10-14T00:00:00+00:00", content="Alpha has value 10", confidence=.9, url="https://example.test/1"),
                Evidence(source="two", title="Alpha conflicting value", retrieved_at="2026-10-14T00:00:00+00:00", content="Alpha has value 20", confidence=.85, url="https://example.test/2"),
            ]
            result = await coordinator.areason(IntelligenceRequest(goal="compare conflicting Alpha values step by step", context={"domain":"test", "focus_entity":"Alpha"}), evidence, Provider(), storage=engine)
            assert result.answer == "parity result"
            assert result.metadata["graph_context_relation_count"] >= 2
            assert result.metadata["episode_id"]
            ok, reason = await graph.averify_integrity_chain(storage=engine)
            assert ok, reason
            rows = await engine.fetchall("SELECT sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash FROM uie_integrity_ledger ORDER BY sequence_id")
            previous = GENESIS_HASH
            for row in rows:
                payload = json.loads(row["payload_canonical_json"])
                canonical = {"sequence_id":row["sequence_id"], "entity_type":row["entity_type"], "entity_id":row["entity_id"], "event_type":row["event_type"], "payload":payload}
                assert row["prev_hash"] == previous
                digest = hashlib.sha256(canonical_json(canonical).encode("utf-8") + previous.encode("ascii")).hexdigest()
                assert digest == row["record_hash"]
                previous = digest
        finally:
            await engine.close()
    asyncio.run(run())
