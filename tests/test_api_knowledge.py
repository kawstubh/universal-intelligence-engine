import os
from fastapi.testclient import TestClient


def test_knowledge_endpoints_fail_closed_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("UIE_API_KEY", raising=False)
    monkeypatch.setenv("UIE_WEB_KNOWLEDGE_PATH", str(tmp_path / "web.sqlite3"))
    monkeypatch.setenv("UIE_INGESTION_QUEUE_PATH", str(tmp_path / "queue.sqlite3"))
    from universal_intelligence_engine.api import app
    client = TestClient(app)
    assert client.get("/v1/knowledge/stats").status_code == 503
    assert client.post("/v1/knowledge/queue/seed").status_code == 503


def test_knowledge_stats_requires_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "test-key")
    monkeypatch.setenv("UIE_WEB_KNOWLEDGE_PATH", str(tmp_path / "web.sqlite3"))
    monkeypatch.setenv("UIE_INGESTION_QUEUE_PATH", str(tmp_path / "queue.sqlite3"))
    from universal_intelligence_engine.api import app
    client = TestClient(app)
    assert client.get("/v1/knowledge/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/v1/knowledge/stats", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert response.json()["corpus"]["documents"] == 0
