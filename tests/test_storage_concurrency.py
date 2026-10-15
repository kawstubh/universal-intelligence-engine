import asyncio
import hashlib
import json
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH

async def _append(engine, entity_id):
    async with engine.transaction() as tx:
        head=await tx.fetchone('SELECT sequence_id,record_hash FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1')
        seq=int(head['sequence_id'])+1 if head else 1
        prev=head['record_hash'] if head else GENESIS_HASH
        payload={'id':entity_id}
        wrapped={'sequence_id':seq,'entity_type':'test','entity_id':entity_id,'event_type':'APPEND','payload':payload}
        digest=hashlib.sha256(canonical_json(wrapped).encode()+prev.encode('ascii')).hexdigest()
        await tx.execute('INSERT INTO uie_integrity_ledger(sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?,?)',(seq,'test',entity_id,'APPEND',canonical_json(payload),prev,digest))

def test_concurrent_ledger_appends_remain_chained(tmp_path):
    async def run():
        engine=StorageEngine(sqlite_path=str(tmp_path/'concurrent.sqlite3')); await engine.connect()
        try:
            await engine.apply_migrations()
            await asyncio.gather(*[_append(engine,f'entity-{i}') for i in range(20)])
            rows=await engine.fetchall('SELECT * FROM uie_integrity_ledger ORDER BY sequence_id')
            assert len(rows)==20
            previous=GENESIS_HASH
            for n,row in enumerate(rows,1):
                assert row['sequence_id']==n and row['prev_hash']==previous
                payload=json.loads(row['payload_canonical_json'])
                wrapped={'sequence_id':n,'entity_type':row['entity_type'],'entity_id':row['entity_id'],'event_type':row['event_type'],'payload':payload}
                digest=hashlib.sha256(canonical_json(wrapped).encode()+previous.encode('ascii')).hexdigest()
                assert row['record_hash']==digest
                previous=digest
        finally: await engine.close()
    asyncio.run(run())

def test_transaction_failure_rolls_back_episode_and_ledger(tmp_path):
    async def run():
        engine=StorageEngine(sqlite_path=str(tmp_path/'rollback.sqlite3')); await engine.connect()
        try:
            await engine.apply_migrations()
            try:
                async with engine.transaction() as tx:
                    await tx.execute('INSERT INTO uie_episodes(id,goal,domain,trace_json,trajectory_scores_json,chosen_path_index,feedback_json,created_at) VALUES(?,?,?,?,?,?,?,?)',('episode-1','simulated','test','{}','[]',None,'{}','now'))
                    await tx.execute('INSERT INTO uie_integrity_ledger(entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?)',('episode','episode-1','INSERT','{}',GENESIS_HASH,'invalid'))
                    raise RuntimeError('simulated crash')
            except RuntimeError as exc:
                assert str(exc)=='simulated crash'
            assert await engine.fetchone('SELECT id FROM uie_episodes WHERE id=?',('episode-1',)) is None
            assert await engine.fetchone('SELECT sequence_id FROM uie_integrity_ledger WHERE entity_id=?',('episode-1',)) is None
        finally: await engine.close()
    asyncio.run(run())

def test_rerunning_migration_preserves_existing_rows(tmp_path):
    async def run():
        engine=StorageEngine(sqlite_path=str(tmp_path/'migration.sqlite3')); await engine.connect()
        try:
            await engine.apply_migrations()
            await engine.execute('INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?)',('keep','Keep','entity','test','now','keep'))
            await engine.apply_migrations(); await engine.apply_migrations()
            row=await engine.fetchone('SELECT name FROM uie_entities WHERE id=?',('keep',))
            assert row['name']=='Keep'
        finally: await engine.close()
    asyncio.run(run())
