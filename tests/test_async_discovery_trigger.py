"""Async curiosity-trigger parity across SQLite and the live PostgreSQL service."""
from __future__ import annotations
import asyncio
import os
import uuid
import pytest
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.query_claims import ClaimQueryService
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH
import hashlib


def test_async_query_enqueues_sparse_evidence_once_and_audits_it(tmp_path):
    async def run():
        db = tmp_path / "discovery-trigger.sqlite3"
        memory = ContradictionEngine(str(db))
        service = ClaimQueryService(memory)
        storage = StorageEngine(url="sqlite:///" + str(db))
        await storage.connect()
        try:
            first = await service.aquery("Unknown Widget", include_graph=False, trigger_learning=True, storage=storage)
            second = await service.aquery("Unknown Widget", include_graph=False, trigger_learning=True, storage=storage)
            assert first["learning_trigger"]["scheduled"] is True
            assert first["learning_trigger"]["queue"]["status"] == "queued"
            assert second["learning_trigger"]["scheduled"] is False
            assert second["learning_trigger"]["status"] == "duplicate_active_mission"
            missions = await storage.fetchall("SELECT * FROM uie_discovery_missions WHERE entity_key=?", ("unknown widget",))
            assert len(missions) == 1
            events = await storage.fetchall("SELECT * FROM uie_integrity_ledger WHERE entity_type='discovery_mission' AND event_type='DISCOVERY_ENQUEUED' AND entity_id=?", (missions[0]["id"],))
            assert len(events) == 1
            event = dict(events[0])
            payload = __import__('json').loads(event["payload_canonical_json"])
            wrapped = {"sequence_id": event["sequence_id"], "entity_type": event["entity_type"], "entity_id": event["entity_id"], "event_type": event["event_type"], "payload": payload}
            head = await storage.fetchone("SELECT prev_hash,record_hash FROM uie_integrity_ledger WHERE sequence_id=?", (event["sequence_id"],))
            assert hashlib.sha256(canonical_json(wrapped).encode() + head["prev_hash"].encode("ascii")).hexdigest() == head["record_hash"]
            assert head["prev_hash"] == GENESIS_HASH
        finally:
            await storage.close()
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("UIE_DATABASE_URL", "").startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")), reason="requires live PostgreSQL CI service")
def test_live_postgres_async_query_enqueues_and_suppresses_duplicates():
    async def run():
        storage = StorageEngine.from_env()
        await storage.connect()
        suffix = uuid.uuid4().hex[:10]
        subject = "Unknown Widget " + suffix
        try:
            await storage.initialize_schema()
            service = ClaimQueryService(ContradictionEngine(".data/postgres-trigger-test.sqlite3"))
            first = await service.aquery(subject, include_graph=False, trigger_learning=True, storage=storage)
            second = await service.aquery(subject, include_graph=False, trigger_learning=True, storage=storage)
            assert first["learning_trigger"]["scheduled"] is True
            assert second["learning_trigger"]["status"] == "duplicate_active_mission"
            mission = await storage.fetchone("SELECT * FROM uie_discovery_missions WHERE entity_key=?", (subject.lower(),))
            assert mission and mission["status"] == "queued"
            ledger = await storage.fetchone("SELECT * FROM uie_integrity_ledger WHERE entity_type='discovery_mission' AND entity_id=? AND event_type='DISCOVERY_ENQUEUED'", (mission["id"],))
            assert ledger and len(ledger["record_hash"]) == 64 and len(ledger["prev_hash"]) == 64
            import json
            payload = json.loads(ledger["payload_canonical_json"])
            wrapped = {"sequence_id": ledger["sequence_id"], "entity_type": ledger["entity_type"], "entity_id": ledger["entity_id"], "event_type": ledger["event_type"], "payload": payload}
            assert hashlib.sha256(canonical_json(wrapped).encode() + ledger["prev_hash"].encode("ascii")).hexdigest() == ledger["record_hash"]
        finally:
            await storage.close()
    asyncio.run(run())
