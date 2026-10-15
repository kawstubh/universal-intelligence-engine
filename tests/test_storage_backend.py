import asyncio
from universal_intelligence_engine.db.storage_engine import StorageEngine, _pg_sql

def test_sqlite_migration_is_idempotent_and_crud(tmp_path):
    async def run():
        engine=StorageEngine(sqlite_path=str(tmp_path/'uie.sqlite3'))
        await engine.connect()
        try:
            await engine.apply_migrations(); await engine.apply_migrations()
            await engine.execute('INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?)',('e1','Alpha','entity','test','now','alpha'))
            row=await engine.fetchone('SELECT name FROM uie_entities WHERE id=?',('e1',))
            assert row['name']=='Alpha'
            await engine.execute('INSERT INTO uie_integrity_ledger(entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?)',('entity','e1','INSERT','{}','genesis','hash1'))
            assert (await engine.fetchone('SELECT record_hash FROM uie_integrity_ledger WHERE entity_id=?',('e1',)))['record_hash']=='hash1'
        finally: await engine.close()
    asyncio.run(run())

def test_postgres_placeholder_translation_ignores_quoted_question_marks():
    assert _pg_sql("SELECT '?' AS literal, value FROM t WHERE id=? AND note=\"?\"") == "SELECT '?' AS literal, value FROM t WHERE id=$1 AND note=\"?\""

def test_postgres_mock_pool_crud_and_ledger():
    class Conn:
        def __init__(self): self.calls=[]
        async def execute(self,sql,*params): self.calls.append((sql,params)); return 'INSERT 0 1'
        async def fetch(self,sql,*params): self.calls.append((sql,params)); return [{'entity_id':params[0],'record_hash':'h1'}]
        def transaction(self):
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def tx(): yield
            return tx()
    class Acquire:
        def __init__(self,c): self.c=c
        async def __aenter__(self): return self.c
        async def __aexit__(self,*args): return False
    class Pool:
        def __init__(self): self.conn=Conn()
        def acquire(self): return Acquire(self.conn)
    async def run():
        pool=Pool(); engine=StorageEngine('postgresql://mock/db',pool=pool)
        result=await engine.execute('INSERT INTO uie_integrity_ledger(entity_id) VALUES(?)',('e1',))
        row=await engine.fetchone('SELECT entity_id,record_hash FROM uie_integrity_ledger WHERE entity_id=?',('e1',))
        assert result.rowcount==1 and row['record_hash']=='h1'
        assert pool.conn.calls[0][0].endswith('VALUES($1)')
    asyncio.run(run())
import asyncio
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.query_claims import ClaimQueryService

def test_async_graph_ingest_and_traversal_use_storage_adapter(tmp_path):
    async def run():
        path=str(tmp_path/'graph.sqlite3'); engine=StorageEngine(sqlite_path=path); await engine.connect()
        try:
            await engine.apply_migrations()
            graph=KnowledgeGraphStore(path)
            saved=await graph.aingest_claim({'id':'c-1','subject':'Alpha','predicate':'connects_to','object_claim':'Beta','confidence':0.8,'provenance_url':'https://example.test/source'},storage=engine)
            assert saved['claim_id']=='c-1'
            result=await graph.aget_entity_subgraph('Alpha',max_hops=2,storage=engine)
            assert result['root']['normalized_name']=='alpha'
            assert len(result['relations'])==1 and result['relations'][0]['target_name']=='Beta'
            assert result['relations'][0]['traversal_confidence']==0.8
            ok,reason=await graph.averify_integrity_chain(storage=engine)
            assert ok,reason
            service=ClaimQueryService(db_path=path)
            query=await service.aquery_graph('Alpha',storage=engine)
            assert len(query['knowledge_graph']['relations'])==1
        finally: await engine.close()
    asyncio.run(run())

def test_async_graph_rollback_does_not_leave_partial_nodes(tmp_path):
    async def run():
        path=str(tmp_path/'rollback-graph.sqlite3'); engine=StorageEngine(sqlite_path=path); await engine.connect()
        try:
            await engine.apply_migrations(); graph=KnowledgeGraphStore(path)
            try:
                async with engine.transaction() as tx:
                    await tx.execute("INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?)",('temp','Temp','entity','test','now','temp'))
                    raise RuntimeError('rollback')
            except RuntimeError: pass
            assert await engine.fetchone('SELECT id FROM uie_entities WHERE id=?',('temp',)) is None
        finally: await engine.close()
    asyncio.run(run())
