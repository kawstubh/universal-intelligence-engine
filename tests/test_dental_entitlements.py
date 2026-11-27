from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from universal_intelligence_engine import dental_entitlements
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def entitlement_store(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "entitlements.sqlite3"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("ALEMBIC_CONFIG", str(root / "alembic.ini"))
    store = DentalStore()
    store.create_organisation(
        {"id": "org-test", "name": "Test Clinic Group", "organisation_type": "clinic"}
    )
    for clinic_id in ("clinic-trial", "clinic-free"):
        store.create_clinic(
            {
                "id": clinic_id,
                "organisation_id": "org-test",
                "name": clinic_id,
                "clinic_type": "clinic",
            }
        )
    monkeypatch.setattr(dental_entitlements, "get_dental_store", lambda: store)
    return store


def test_new_clinic_gets_one_72_hour_trial(entitlement_store, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(dental_entitlements.time, "time", lambda: now)
    first = dental_entitlements.ensure_clinic_entitlement("clinic-trial")
    second = dental_entitlements.ensure_clinic_entitlement("clinic-trial")

    assert first["status"] == "trialing"
    assert first["trial_started_at"] == now
    assert first["trial_ends_at"] == now + 72 * 60 * 60
    assert second["trial_started_at"] == first["trial_started_at"]
    assert second["trial_ends_at"] == first["trial_ends_at"]


def test_founding_clinic_is_immutable_lifetime_free(entitlement_store, monkeypatch):
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-free")
    first = dental_entitlements.ensure_clinic_entitlement("clinic-free")
    second = dental_entitlements.ensure_clinic_entitlement("clinic-free")

    assert first["entitlement_type"] == "founding_free"
    assert first["status"] == "lifetime_free"
    assert first["trial_started_at"] is None
    assert first["trial_ends_at"] is None
    assert second["status"] == "lifetime_free"


def test_expired_trial_is_blocked_server_side(entitlement_store, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(dental_entitlements.time, "time", lambda: now)
    dental_entitlements.ensure_clinic_entitlement("clinic-trial")
    monkeypatch.setattr(dental_entitlements.time, "time", lambda: now + 72 * 60 * 60)

    with pytest.raises(HTTPException) as exc:
        dental_entitlements.require_entitled("clinic-trial")

    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "CLINIC_SUBSCRIPTION_REQUIRED"


def test_paid_activation_converts_trial_to_subscription(entitlement_store, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(dental_entitlements.time, "time", lambda: now)
    dental_entitlements.ensure_clinic_entitlement("clinic-trial")

    active = dental_entitlements.activate_paid_entitlement(
        "clinic-trial", "clinic", "plink_test_123"
    )

    assert active["entitlement_type"] == "subscription"
    assert active["status"] == "active"
    assert active["plan_id"] == "clinic"
    assert active["source_order_id"] == "plink_test_123"
    assert active["current_period_end"] == now + 30 * 86400
