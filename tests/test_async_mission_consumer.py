"""Async discovery queue consumer parity tests for SQLite and live PostgreSQL."""
from __future__ import annotations
import asyncio, json, os, uuid
from types import SimpleNamespace
import pytest
from universal_intelligence_engine.db.storage_engine import StorageEngine
from universal_intelligence_engine.learning.autonomous_learner import AutonomousLearner
from universal_intelligence_engine.learning.contradiction import ContradictionEngine
from universal_intelligence_engine.learning.epistemic_dispatcher import EpistemicDiscoveryDispatcher

class FakeStore:
    def ingest_url(self, url): return {"url": url, "canonical_url": url, "status": "stored"}
    def get_document(self, url): return {"canonical_url": url, "content": "Acme Widget is a durable component used by many engineering teams."}
class FakeDistiller:
    def distill(self, content, url, domain):
        return [SimpleNamespace(subject="Acme Widget", predicate="is", object_claim="a durable component used by many engineering teams", temporal_anchor=None, confidence=0.62, provenance_url=url, domain=domain, status="unverified_claim", to_dict=lambda: {"subject":"Acme Widget", "predicate":"is", "object_claim":"a durable component used by many engineering teams", "temporal_anchor":None, "confidence":0.62, "provenance_url":url, "domain":domain, "status":"unverified_claim"})]
class FakePipeline:
    seed_path = None
    store = FakeStore()
    distiller = FakeDistiller()

def learner(db):
    obj = AutonomousLearner.__new__(AutonomousLearner)
    obj.pipeline = FakePipeline()
    obj.claim_memory = ContradictionEngine(str(db))
    return obj

def enqueue(db, engine, entity="Acme Widget"):
    dispatcher = EpistemicDiscoveryDispatcher(str(db))
    result = dispatcher.enqueue(focus_entity=entity, query_text="technology software component", target_domains=["technology_science"])
    assert result["enqueued"]
    return result["mission_id"]

def test_sqlite_async_consumer_harvests_claims_graph_and_completion_ledger(tmp_path):
    async def run():
        db=tmp_path/'consumer.sqlite3'; engine=StorageEngine(url='sqlite:///'+str(db)); await engine.connect(); await engine.initialize_schema()
        try:
            mid=enqueue(db,engine)
            result=await learner(db).drain_and_harvest_missions_async(storage=engine)
            assert result['missions_claimed']==1 and result['claims_upserted']>=1 and result['graph_relations_added']>=1
            mission=await engine.fetchone('SELECT status,result_json FROM uie_discovery_missions WHERE id=?',(mid,))
            assert mission['status']=='COMPLETED'
            assert json.loads(mission['result_json'])['claims_upserted']>=1
            assert await engine.fetchone("SELECT id FROM claims WHERE subject='Acme Widget'")
            assert await engine.fetchone("SELECT r.id FROM uie_relations r JOIN uie_entities e ON e.id=r.source_entity_id WHERE e.name='Acme Widget'")
            event=await engine.fetchone("SELECT event_type FROM uie_integrity_ledger WHERE entity_id=? AND event_type='DISCOVERY_COMPLETED'",(mid,))
            assert event and event['event_type']=='DISCOVERY_COMPLETED'
            again=await learner(db).drain_and_harvest_missions_async(storage=engine)
            assert again['missions_claimed']==0
        finally: await engine.close()
    asyncio.run(run())

@pytest.mark.skipif(not os.getenv('UIE_DATABASE_URL','').startswith(('postgres://','postgresql://','postgresql+asyncpg://')), reason='requires live PostgreSQL 16 CI service')
def test_live_postgres_consumer_concurrent_workers_claim_once_and_harvest(tmp_path):
    async def run():
        engine=StorageEngine.from_env(); await engine.connect(); await engine.initialize_schema()
        db=tmp_path/'worker.sqlite3'; mid=None
        try:
            suffix=uuid.uuid4().hex[:10]
            dispatcher=EpistemicDiscoveryDispatcher(str(db))
            item=dispatcher.enqueue(focus_entity='PG Widget '+suffix,query_text='technology software component',target_domains=['technology_science'])
            # Move the test mission into the PostgreSQL queue, not the local SQLite queue.
            now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(); mid=item['mission_id']
            await engine.execute("INSERT INTO uie_discovery_missions(id,focus_entity,entity_key,query_text,target_domains_json,seed_allowlist_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)",(mid,'PG Widget '+suffix,('pg widget '+suffix), 'technology software component',json.dumps(['technology_science']),json.dumps(item['seed_allowlist'][:1]),now,now))
            a,b=await asyncio.gather(learner(db).drain_and_harvest_missions_async(storage=engine),learner(db).drain_and_harvest_missions_async(storage=engine))
            assert a['missions_claimed']+b['missions_claimed']==1
            row=await engine.fetchone('SELECT status FROM uie_discovery_missions WHERE id=?',(mid,)); assert row['status']=='COMPLETED'
            assert await engine.fetchone("SELECT id FROM claims WHERE subject='Acme Widget'")
            assert await engine.fetchone("SELECT event_type FROM uie_integrity_ledger WHERE entity_id=? AND event_type='DISCOVERY_COMPLETED'",(mid,))
        finally: await engine.close()
    asyncio.run(run())



