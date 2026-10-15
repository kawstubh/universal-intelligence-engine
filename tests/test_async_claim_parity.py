import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.distiller import DistilledClaim
from universal_intelligence_engine.learning.query_claims import ClaimQueryService
from universal_intelligence_engine.learning.graph_store import KnowledgeGraphStore
from universal_intelligence_engine.learning.heuristic_memory import HeuristicMemory
from universal_intelligence_engine.security.provenance_crypto import canonical_json, GENESIS_HASH, calculate_record_hash


def _claim(subject, obj, url, year, confidence):
    return DistilledClaim(subject=subject, predicate='reported', object_claim=obj, temporal_anchor=year,
        confidence=confidence, provenance_url=url, domain='markets_finance_industry', status='unverified_claim')


def test_async_query_matches_legacy_claim_filtering_temporal_and_provenance(tmp_path):
    async def run():
        db=tmp_path/'claim-parity.sqlite3'
        memory=ContradictionEngine(str(db), established_confidence=0.80)
        old=memory.process_claim(_claim('Orion Systems','revenue $4.2B','https://sec.gov/2024', 'as of 2024', .84))
        new=memory.process_claim(_claim('Orion Systems','revenue $3.8B','https://reuters.com/2026', 'as of 2026', .68))
        other=memory.process_claim(_claim('Orion Systems','staff 120 employees','https://sec.gov/staff', 'as of 2025', .42))
        memory.process_claim(_claim('Nova Holdings','revenue $9B','https://sec.gov/nova', None, .84))
        memory.process_claim(_claim('Nova Holdings','revenue $7B','https://reuters.com/nova', None, .68))
        service=ClaimQueryService(memory)
        legacy=service.query('Orion Systems',include_graph=False,min_confidence=.40)
        adapter=StorageEngine(url='sqlite:///' + str(db)); await adapter.connect()
        try:
            async_result=await service.aquery('Orion Systems',include_graph=False,min_confidence=.40,storage=adapter)
            assert async_result['claims']==legacy['claims']
            assert async_result['count']==3
            assert async_result['claims'][0]['temporal_anchor']=='as of 2024'
            assert async_result['claims'][-1]['temporal_anchor']=='as of 2026'
            assert async_result['provenance_ledger']
            assert all(item.get('record_hash') and item.get('prev_hash') for item in async_result['provenance_ledger'])
            filtered=await service.aquery('Orion Systems',include_graph=False,min_confidence=.65,statuses={'unverified_claim'},storage=adapter)
            assert {c['id'] for c in filtered['claims']}=={new['id']}
            contradictions=await service.aquery('Nova Holdings',include_graph=False,storage=adapter)
            assert contradictions['contradictions']
            assert any(c['status']=='conflicted_evidence' for c in contradictions['claims'])
        finally: await adapter.close()
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv('UIE_DATABASE_URL','').startswith(('postgres://','postgresql://','postgresql+asyncpg://')),reason='requires live PostgreSQL CI service')
def test_async_claim_parity_live_postgres_conflict_temporal_provenance():
    async def run():
        adapter=StorageEngine.from_env(); await adapter.connect()
        suffix=uuid.uuid4().hex
        subject='UIE parity '+suffix
        try:
            await adapter.initialize_schema()
            records=[
                (str(uuid.uuid4()),subject,'reported','$4.2B revenue','as of 2024',.84,'https://sec.gov/a','markets_finance_industry','superseded_temporal','["https://sec.gov/a"]','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00'),
                (str(uuid.uuid4()),subject,'reported','$3.8B revenue','as of 2026',.68,'https://reuters.com/b','markets_finance_industry','conflicted_evidence','["https://reuters.com/b"]','2026-02-01T00:00:00+00:00','2026-02-01T00:00:00+00:00')]
            async with adapter.transaction() as tx:
                for row in records:
                    await tx.execute('INSERT INTO claims(id,subject,predicate,object_claim,temporal_anchor,confidence,provenance_url,domain,status,provenance_urls,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',row)
                    claim_id=row[0]
                    payload={'id':claim_id,'subject':subject,'predicate':'reported','object_claim':row[3],'temporal_anchor':row[4],'confidence':row[5],'provenance_url':row[6],'domain':row[7],'status':row[8],'provenance_urls':json.loads(row[9]),'created_at':row[10],'updated_at':row[11]}
                    head=await tx.fetchone('SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1')
                    previous=head['record_hash'] if head else GENESIS_HASH
                    wrapped={'record_type':'claim','record_id':claim_id,'payload':payload}
                    digest=calculate_record_hash(wrapped,previous)
                    await tx.execute('INSERT INTO memory_chain(record_type,record_id,payload_json,prev_hash,record_hash,created_at) VALUES(?,?,?,?,?,?)',('claim',claim_id,canonical_json(wrapped),previous,digest,row[10]))
            service=ClaimQueryService(ContradictionEngine(str(Path('.data')/('pg-parity-'+suffix+'.sqlite3'))))
            result=await service.aquery(subject,include_graph=False,storage=adapter)
            assert result['count']==2
            assert result['claims'][0]['temporal_anchor']=='as of 2024'
            assert result['claims'][1]['temporal_anchor']=='as of 2026'
            assert len(result['provenance_ledger'])==2
            assert all(r['record_hash'] and r['prev_hash'] for r in result['provenance_ledger'])
        finally:
            await adapter.execute('DELETE FROM memory_chain WHERE record_id IN (SELECT id FROM claims WHERE subject=?)',(subject,))
            await adapter.execute('DELETE FROM claims WHERE subject=?',(subject,))
            await adapter.close()
    asyncio.run(run())


def test_async_graph_reflexion_avoids_sync_store_constructors(tmp_path, monkeypatch):
    async def run():
        db=tmp_path/'async-graph.sqlite3'
        memory=ContradictionEngine(str(db))
        memory.process_claim(_claim('Async Entity','is established','https://sec.gov/a',None,.84))
        memory.process_claim(_claim('Async Entity','is not established','https://reuters.com/b',None,.68))
        service=ClaimQueryService(memory)
        def forbidden(*args, **kwargs): raise AssertionError('synchronous SQLite store constructor invoked from aquery')
        monkeypatch.setattr(KnowledgeGraphStore,'__init__',forbidden)
        monkeypatch.setattr(HeuristicMemory,'__init__',forbidden)
        adapter=StorageEngine(url='sqlite:///' + str(db)); await adapter.connect()
        try:
            result=await service.aquery('Async Entity',storage=adapter)
            assert result['knowledge_graph']['relations']
            assert 'graph_reflexion' in result
        finally: await adapter.close()
    asyncio.run(run())

