from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from universal_intelligence_engine.dental_rate_limit import enforce_shared_rate_limit
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def shared_rate_limit_stores(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "DENTAL_SQLITE_PATH", str(tmp_path / "shared-rate-limit.sqlite3")
    )
    monkeypatch.setenv(
        "DENTAL_RATE_LIMIT_HASH_KEY",
        "synthetic-oralyn-rate-limit-key-at-least-32-bytes",
    )
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    first = DentalStore()
    second = DentalStore()
    return first, second


def _attempt(store, *, scope="doctor_login", identity="203.0.113.40", now=1000):
    enforce_shared_rate_limit(
        store,
        scope=scope,
        client_identity=identity,
        max_attempts=10,
        window_seconds=300,
        rate_limited_code="DOCTOR_LOGIN_RATE_LIMITED",
        now=now,
    )


def test_shared_counter_is_visible_across_independent_store_instances(shared_rate_limit_stores):
    first, second = shared_rate_limit_stores
    for _ in range(5):
        _attempt(first)
    for _ in range(5):
        _attempt(second)

    with pytest.raises(HTTPException) as limited:
        _attempt(first)

    assert limited.value.status_code == 429
    assert limited.value.detail["code"] == "DOCTOR_LOGIN_RATE_LIMITED"
    assert limited.value.headers["Retry-After"] == "200"


def test_rate_limit_scopes_are_isolated_and_window_rolls_forward(shared_rate_limit_stores):
    first, second = shared_rate_limit_stores
    for _ in range(10):
        _attempt(first, scope="doctor_login")
    _attempt(second, scope="assistant_login")

    with pytest.raises(HTTPException):
        _attempt(second, scope="doctor_login")

    # The next fixed window starts at 1200 and receives a fresh counter.
    _attempt(second, scope="doctor_login", now=1200)


def test_rate_limit_persists_only_keyed_identity_digest(shared_rate_limit_stores):
    first, _second = shared_rate_limit_stores
    _attempt(first, identity="203.0.113.41")
    rows = first._execute(
        "SELECT key_hash FROM dental_rate_limit_buckets"
    )
    assert len(rows) == 1
    assert len(rows[0]["key_hash"]) == 64
    assert "203.0.113.41" not in rows[0]["key_hash"]


def test_postgres_rate_limiter_fails_closed_without_hash_secret(monkeypatch):
    monkeypatch.delenv("DENTAL_RATE_LIMIT_HASH_KEY", raising=False)
    monkeypatch.delenv("DENTAL_PUBLIC_BOOKING_HASH_KEY", raising=False)

    class PostgresStore:
        database_url = "postgresql://synthetic.invalid/db"

        def _execute(self, *_args, **_kwargs):
            raise AssertionError("must fail before touching the database")

    with pytest.raises(HTTPException) as unavailable:
        enforce_shared_rate_limit(
            PostgresStore(),
            scope="doctor_login",
            client_identity="203.0.113.42",
            max_attempts=10,
            window_seconds=300,
            now=1000,
        )

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "RATE_LIMIT_NOT_CONFIGURED"


def test_shared_store_failure_fails_closed(monkeypatch):
    monkeypatch.setenv(
        "DENTAL_RATE_LIMIT_HASH_KEY",
        "synthetic-oralyn-rate-limit-key-at-least-32-bytes",
    )

    class UnavailableStore:
        database_url = "postgresql://synthetic.invalid/db"

        def _execute(self, *_args, **_kwargs):
            raise OSError("synthetic database outage")

    with pytest.raises(HTTPException) as unavailable:
        enforce_shared_rate_limit(
            UnavailableStore(),
            scope="assistant_login",
            client_identity="203.0.113.43",
            max_attempts=10,
            window_seconds=300,
            now=1000,
        )

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "RATE_LIMIT_STORE_UNAVAILABLE"



def test_postgres_counter_is_shared_across_independent_store_instances(monkeypatch):
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("PostgreSQL CI service is not configured")

    # The PostgreSQL workflow provisions an isolated disposable service. Create
    # only the rate-limit table here; the separate migration rehearsal verifies
    # Alembic creates it at revision 0016.
    engine = sa.create_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS dental_rate_limit_buckets ("
                "key_hash VARCHAR(64) PRIMARY KEY, "
                "window_start BIGINT NOT NULL, "
                "attempt_count INTEGER NOT NULL CHECK (attempt_count > 0), "
                "updated_at BIGINT NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_dental_rate_limit_window_start "
                "ON dental_rate_limit_buckets(window_start)"
            )
        )

    database_url = test_database_url.replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "false")
    monkeypatch.setenv(
        "DENTAL_RATE_LIMIT_HASH_KEY",
        "synthetic-postgres-rate-limit-key-at-least-32-bytes",
    )

    first = DentalStore()
    second = DentalStore()
    identity = "postgres-ci-" + uuid.uuid4().hex

    for _ in range(2):
        enforce_shared_rate_limit(
            first,
            scope="assistant_login",
            client_identity=identity,
            max_attempts=2,
            window_seconds=300,
            rate_limited_code="ASSISTANT_LOGIN_RATE_LIMITED",
            now=1_000,
        )

    with pytest.raises(HTTPException) as limited:
        enforce_shared_rate_limit(
            second,
            scope="assistant_login",
            client_identity=identity,
            max_attempts=2,
            window_seconds=300,
            rate_limited_code="ASSISTANT_LOGIN_RATE_LIMITED",
            now=1_000,
        )

    assert limited.value.status_code == 429
    assert limited.value.detail["code"] == "ASSISTANT_LOGIN_RATE_LIMITED"
    engine.dispose()
