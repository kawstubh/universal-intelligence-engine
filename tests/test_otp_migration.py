import sqlite3
from universal_intelligence_engine.dental_store import DentalStore


def test_otp_table_removed_by_head_migration(tmp_path, monkeypatch):
    path = tmp_path / "otp-removal.sqlite3"
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    store = DentalStore()
    rows = store._execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dental_otp_challenges'")
    assert rows == []
