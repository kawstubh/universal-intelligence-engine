"""Real PostgreSQL integration tests. CI supplies UIE_DATABASE_URL and a postgres service."""
import asyncio
import hashlib
import json
import os
import uuid

import pytest

from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH

pytestmark = pytest.mark.skipif(not os.getenv("UIE_DATABASE_URL", "").startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")),
                                reason="requires live PostgreSQL service and UIE_DATABASE_URL")


def test_live_postgres_schema_concurrency_upsert_advisory_lock_and_hash_chain():
    async def run():
        engine = StorageEngine.from_env()
        await engine.connect()
        suffix = uuid.uuid4().hex
        entity_id = "pg-" + suffix
        try:
            await engine.initialize_schema()
            # Real ON CONFLICT behavior and transaction isolation.
            async def upsert(name):
                async with engine.transaction() as tx:
                    await tx.execute("INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?) ON CONFLICT(normalized_name) DO UPDATE SET name=excluded.name", (entity_id, name, "entity", "integration", "now", "normalized-" + suffix))
            await asyncio.gather(upsert("first"), upsert("last"))
            row = await engine.fetchone("SELECT id,name FROM uie_entities WHERE normalized_name=?", ("normalized-" + suffix,))
            assert row["id"] == entity_id and row["name"] in {"first", "last"}
            # Exercise PostgreSQL advisory locks on a real asyncpg connection.
            async with engine._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "uie-test-" + suffix)
                    async with engine._pool.acquire() as contender:
                        locked = await contender.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", "uie-test-" + suffix)
                        assert locked is False
            # Two concurrent ledger appends use serialized transactional head reads;
            # assert every resulting digest is bit-for-bit reproducible.
            async def append(index):
                event_id = f"{entity_id}-{index}"
                async with engine.transaction() as tx:
                    await tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("uie-ledger-chain",))
                    head = await tx.fetchone("SELECT sequence_id,record_hash FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1")
                    prev = head["record_hash"] if head else GENESIS_HASH
                    seq = int(head["sequence_id"]) + 1 if head else 1
                    payload = {"id": event_id}
                    canonical = {"sequence_id":seq,"entity_type":"test","entity_id":event_id,"event_type":"APPEND","payload":payload}
                    digest = hashlib.sha256(canonical_json(canonical).encode("utf-8") + prev.encode("ascii")).hexdigest()
                    await tx.execute("INSERT INTO uie_integrity_ledger(sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?,?)", (seq,"test",event_id,"APPEND",canonical_json(payload),prev,digest))
            await asyncio.gather(append(1), append(2))
            rows = await engine.fetchall("SELECT sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash FROM uie_integrity_ledger WHERE entity_type='test' ORDER BY sequence_id")
            for row in rows:
                payload = json.loads(row["payload_canonical_json"])
                wrapped = {"sequence_id":row["sequence_id"],"entity_type":row["entity_type"],"entity_id":row["entity_id"],"event_type":row["event_type"],"payload":payload}
                expected = hashlib.sha256(canonical_json(wrapped).encode("utf-8") + row["prev_hash"].encode("ascii")).hexdigest()
                assert expected == row["record_hash"]
        finally:
            await engine.close()
    asyncio.run(run())
