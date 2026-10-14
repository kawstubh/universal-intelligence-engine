from universal_intelligence_engine.ingestion.pipeline import CrossDomainIngestionPipeline, classify_domain
from universal_intelligence_engine.web_knowledge import PublicWebKnowledgeStore
from unittest.mock import patch, MagicMock


def test_domain_classification_uses_host_boundaries():
    assert classify_domain('https://arxiv.org/abs/2301.00000') == 'technology_science'
    assert classify_domain('https://www.who.int/news-room') == 'health_life_sciences'
    assert classify_domain('https://evilreuters.com/story') == 'general_reference'


def test_queue_deduplicates_and_rejects_unapproved_urls(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / 'corpus.sqlite3'), min_fetch_interval=0)
    pipeline = CrossDomainIngestionPipeline(store, str(tmp_path / 'queue.sqlite3'))
    result = pipeline.enqueue(['https://arxiv.org/abs/2301.00000', 'https://arxiv.org/abs/2301.00000', 'https://example.com/x'])
    assert result['queued'] == 1
    assert result['rejected'] == 1
    assert pipeline.stats()['total'] == 1


def test_discovery_is_bounded_and_only_queues_approved_hosts(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / 'corpus.sqlite3'), min_fetch_interval=0)
    seed = tmp_path / 'seeds.json'
    seed.write_text('{"technology_science":["https://arxiv.org/sitemap.xml"]}', encoding='utf-8')
    pipeline = CrossDomainIngestionPipeline(store, str(tmp_path / 'queue.sqlite3'), str(seed))
    response = MagicMock()
    response.geturl.return_value = 'https://arxiv.org/sitemap.xml'
    response.read.return_value = b'<urlset><url><loc>https://arxiv.org/abs/2301.00000</loc></url><url><loc>https://evil.example/x</loc></url></urlset>'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch.object(store, '_robots_allows', return_value=True), patch('universal_intelligence_engine.ingestion.pipeline.urlopen', return_value=response):
        result = pipeline.discover(max_sources=1, max_urls_per_source=1)
    assert result['sources_checked'] == 1
    assert result['queued_new'] == 1
    assert result['queue']['queued'] == 1
