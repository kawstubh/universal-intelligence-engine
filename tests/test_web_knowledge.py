from universal_intelligence_engine.web_knowledge import PublicWebKnowledgeStore
import pytest


def test_web_corpus_persists_provenance_and_retrieves_by_query(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO web_documents
            (url,canonical_url,title,content,content_sha256,fetched_at,http_status,content_type,source_domain)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            ("https://docs.python.org/3/library/sqlite3.html",
             "https://docs.python.org/3/library/sqlite3.html", "SQLite database API",
             "SQLite database transactions and persistent storage behavior for Python applications.",
             "abc123", "2026-10-14T00:00:00+00:00", 200, "text/html", "docs.python.org"),
        )
    reopened = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    hits = reopened.search("SQLite persistent storage", limit=3)
    assert len(hits) == 1
    assert hits[0].source == "uie-persistent-web-corpus"
    assert hits[0].url == "https://docs.python.org/3/library/sqlite3.html"
    assert hits[0].metadata["content_sha256"] == "abc123"
    assert reopened.stats()["documents"] == 1


def test_web_corpus_rejects_non_allowlisted_hosts_without_fetch(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    with pytest.raises(ValueError, match="allowlist"):
        store._validate_url("https://example.com/anything")


def test_web_corpus_requires_https(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    with pytest.raises(ValueError, match="HTTPS"):
        store._validate_url("http://docs.python.org/3/")
from universal_intelligence_engine.web_knowledge import DEFAULT_ALLOWED_DOMAINS


def test_source_allowlist_covers_multiple_major_knowledge_domains():
    required = {
        "who.int", "defense.gov", "un.org", "reuters.com", "nasa.gov",
        "sec.gov", "rbi.org.in", "fao.org", "developer.mozilla.org",
    }
    assert required.issubset(DEFAULT_ALLOWED_DOMAINS)


def test_web_corpus_rejects_nonstandard_ports_and_oversized_urls(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    with pytest.raises(ValueError, match="port"):
        store._validate_url("https://docs.python.org:8443/3/")
    with pytest.raises(ValueError, match="2048"):
        store._validate_url("https://docs.python.org/" + "a" * 2100)
