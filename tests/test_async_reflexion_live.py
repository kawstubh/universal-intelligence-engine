import asyncio
import json
import os
import pytest
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.reasoning.tool_reflexion import ToolReflexionEngine
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH


def test_async_reflexion_failure_rule_transaction_and_no_sync_sqlite(tmp_path, monkeypatch):
    async def run():
        engine=StorageEngine(sqlite_path=str(tmp_path/'reflexion.sqlite3'))
        await engine.connect()
        try:
            await engine.initialize_schema()
            memory=HeuristicMemory(str(tmp_path/'legacy-unused.sqlite3'))
            monkeypatch.setattr(memory, '_connect', lambda: (_ for _ in ()).throw(AssertionError('sync SQLite fallback invoked')))
            reflexion=ToolReflexionEngine(memory)
            async def failed_tool(): raise RuntimeError('HTTP request timed out')
            with pytest.raises(RuntimeError, match='timed out'):
                await reflexion.aexecute('europe-pmc','cancer gene',failed_tool,storage=engine)
            rules=await reflexion.aapplicable_rules('europe-pmc','cancer gene',storage=engine)
            assert any(item['predicate']=='shorten_query_after_timeout' for item in rules)
            assert (await reflexion.aprepare_query('europe-pmc','cancer gene',storage=engine)) == 'cancer gene'
            row=await engine.fetchone("SELECT * FROM uie_integrity_ledger WHERE entity_type='heuristic' ORDER BY sequence_id DESC LIMIT 1")
            assert row and row['event_type']=='INSERT'
            payload=json.loads(row['payload_canonical_json'])
            wrapped={'sequence_id':row['sequence_id'],'entity_type':row['entity_type'],'entity_id':row['entity_id'],'event_type':row['event_type'],'payload':payload}
            import hashlib
            assert hashlib.sha256(canonical_json(wrapped).encode()+row['prev_hash'].encode('ascii')).hexdigest()==row['record_hash']
            chain=await engine.fetchall('SELECT * FROM memory_chain ORDER BY sequence')
            previous=GENESIS_HASH
            for item in chain:
                assert item['prev_hash']==previous
                previous=item['record_hash']
        finally: await engine.close()
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv('UIE_DATABASE_URL','').startswith(('postgres://','postgresql://','postgresql+asyncpg://')), reason='requires live PostgreSQL CI service')
def test_live_postgres_async_reflexion_failure_rule_and_ledger():
    async def run():
        engine=StorageEngine.from_env(); await engine.connect()
        try:
            await engine.initialize_schema()
            memory=HeuristicMemory('.data/unused-sync-fallback.sqlite3')
            memory._connect=lambda: (_ for _ in ()).throw(AssertionError('sync SQLite fallback invoked'))
            reflexion=ToolReflexionEngine(memory)
            query='integration test and gene '+os.urandom(4).hex()
            async def fail(): raise ValueError('lowercase boolean syntax error')
            with pytest.raises(ValueError,match='syntax error'):
                await reflexion.aexecute('europe-pmc',query,fail,storage=engine)
            rules=await reflexion.aapplicable_rules('europe-pmc',query,storage=engine)
            assert any(item['predicate']=='uppercase_boolean_operators' for item in rules)
            assert await reflexion.aprepare_query('europe-pmc',query,storage=engine) == query.replace(' and ',' AND ')
            row=await engine.fetchone("SELECT * FROM uie_integrity_ledger WHERE entity_type='heuristic' AND entity_id=(SELECT id FROM heuristic_rules WHERE subject=? ORDER BY updated_at DESC LIMIT 1) ORDER BY sequence_id DESC LIMIT 1",('query:'+reflexion._query_key(query),))
            assert row and row['event_type']=='INSERT'
        finally: await engine.close()
    asyncio.run(run())


