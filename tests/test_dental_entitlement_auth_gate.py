from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_auth, dental_entitlements
from universal_intelligence_engine.dental_store import DentalStore


def test_expired_trial_blocks_clinic_routes_but_allows_billing_reactivation(tmp_path, monkeypatch):
    now = 1_800_000_000
    database_path = tmp_path / "entitlement-auth.sqlite3"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DENTAL_MASTER_CLINIC_ID", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("ALEMBIC_CONFIG", str(root / "alembic.ini"))
    monkeypatch.setattr(dental_auth.time, "time", lambda: now)
    store = DentalStore()
    store.create_organisation(
        {"id": "org-a", "name": "Clinic A", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Clinic A",
            "clinic_type": "clinic",
        }
    )
    store.create_doctor(
        {
            "id": "doctor-a",
            "email": "doctor@example.test",
            "password_hash": dental_auth.hash_password("a-long-test-password"),
            "clinic_id": "clinic-a",
            "role": "doctor",
        }
    )
    store.create_doctor_membership("doctor-a", "clinic-a", "owner")
    token = "a-test-session-token-that-is-long-enough"
    store.create_session(
        "doctor-a|clinic-a|owner",
        dental_auth._hash(token),
        now + 30 * 86400,
    )
    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_entitlements, "get_dental_store", lambda: store)
    dental_entitlements.ensure_clinic_entitlement("clinic-a")

    app = FastAPI()

    @app.get("/v1/dental/patients")
    async def protected_patients(actor=Depends(dental_auth.require_doctor)):
        return {"clinic_id": actor["clinic_id"]}

    @app.get("/v1/dental/billing/membership")
    async def billing_membership(actor=Depends(dental_auth.require_doctor)):
        return {"clinic_id": actor["clinic_id"]}

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(
        dental_auth.time, "time", lambda: now + dental_entitlements.TRIAL_SECONDS
    )
    blocked = client.get("/v1/dental/patients", headers=headers)
    billing = client.get("/v1/dental/billing/membership", headers=headers)

    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "CLINIC_SUBSCRIPTION_REQUIRED"
    assert billing.status_code == 200
