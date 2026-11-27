from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_auth, dental_entitlements
from universal_intelligence_engine.dental_store import DentalStore


def _master_admin_client(tmp_path: Path, monkeypatch):
    now = 1_800_000_000
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "master-admin.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("ALEMBIC_CONFIG", str(root / "alembic.ini"))
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "dr-pranali")
    monkeypatch.setenv("ORALYN_MASTER_ADMIN_EMAIL", "platform-admin@oralyn.test")
    monkeypatch.setattr(dental_auth.time, "time", lambda: now)

    store = DentalStore()
    store.create_organisation(
        {"id": "org-pranali", "name": "Dr Pranali Dental Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "dr-pranali",
            "organisation_id": "org-pranali",
            "name": "Dr Pranali Dental Clinic",
            "clinic_type": "clinic",
        }
    )
    store.create_doctor(
        {
            "id": "platform-admin",
            "email": "platform-admin@oralyn.test",
            "phone": "9876543210",
            "password_hash": dental_auth.hash_password("initial-admin-password-123"),
            "role": "owner",
            "clinic_id": "dr-pranali",
        }
    )
    store.create_doctor_membership("platform-admin", "dr-pranali", "owner")
    session_token = "oralyn-master-admin-test-session-token"
    store.create_session(
        "platform-admin|dr-pranali|owner",
        dental_auth._hash(session_token),
        now + 30 * 86400,
    )

    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_entitlements, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "enforce_shared_rate_limit", lambda *args, **kwargs: None)

    app = FastAPI()
    app.include_router(dental_api.router)
    return TestClient(app), store, session_token, now


def test_master_admin_provisions_verified_demo_and_owner_activates_once(tmp_path, monkeypatch):
    client, store, session_token, now = _master_admin_client(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {session_token}"}
    demo_payload = {
        "idempotency_key": "master-admin-demo-request-0001",
        "clinic_name": "Sample Smile Dental",
        "owner_name": "Clinic Owner",
        "email": "owner@sample-smile.test",
        "phone": "9876543211",
    }

    received = client.post("/v1/dental/public/demo-requests", json=demo_payload)
    request_row = store._execute(
        "SELECT id,status FROM dental_onboarding_requests WHERE email=?",
        ("owner@sample-smile.test",),
    )[0]
    requests = client.get("/v1/dental/master-admin/demo-requests", headers=headers)
    provisioned = client.post(
        f"/v1/dental/master-admin/demo-requests/{request_row['id']}/provision",
        headers=headers,
        json={"verification_confirmed": True},
    )

    assert received.status_code == 202
    assert requests.status_code == 200
    assert len(requests.json()["requests"]) == 1
    assert provisioned.status_code == 201
    provision = provisioned.json()
    assert provision["status"] == "provisioned"
    assert provision["owner_email"] == "owner@sample-smile.test"
    assert provision["trial_started_at"] == now
    assert provision["trial_ends_at"] == now + 72 * 60 * 60

    entitlement = store._execute(
        "SELECT entitlement_type,status,trial_started_at,trial_ends_at FROM dental_clinic_entitlements WHERE clinic_id=?",
        (provision["clinic_id"],),
    )[0]
    assert entitlement["entitlement_type"] == "trial"
    assert entitlement["status"] == "trialing"
    assert entitlement["trial_ends_at"] == now + 72 * 60 * 60

    activated = client.post(
        "/v1/dental/public/owner-activate",
        json={
            "token": provision["activation_token"],
            "password": "new-owner-password-123",
        },
    )
    owner = store.doctor_by_email("owner@sample-smile.test")
    assert activated.status_code == 200
    assert activated.json()["clinic_id"] == provision["clinic_id"]
    assert dental_auth.verify_password("new-owner-password-123", owner["password_hash"])

    replay = client.post(
        "/v1/dental/public/owner-activate",
        json={
            "token": provision["activation_token"],
            "password": "another-owner-password-456",
        },
    )
    duplicate = client.post(
        f"/v1/dental/master-admin/demo-requests/{request_row['id']}/provision",
        headers=headers,
        json={"verification_confirmed": True},
    )
    clinics = client.get("/v1/dental/master-admin/clinics", headers=headers)

    assert replay.status_code == 410
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_provisioned"
    assert clinics.status_code == 200
    assert len(clinics.json()["clinics"]) == 2


def test_master_admin_routes_fail_closed_without_configured_admin_email(tmp_path, monkeypatch):
    client, _, session_token, _ = _master_admin_client(tmp_path, monkeypatch)
    monkeypatch.delenv("ORALYN_MASTER_ADMIN_EMAIL", raising=False)
    response = client.get(
        "/v1/dental/master-admin/demo-requests",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MASTER_ADMIN_NOT_CONFIGURED"
