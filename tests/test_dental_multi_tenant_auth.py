from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_auth
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def auth_environment(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "multi-tenant-auth.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    store.create_organisation(
        {"id": "org-a", "name": "Clinic Group", "organisation_type": "group"}
    )
    store.create_organisation(
        {"id": "org-b", "name": "Other Clinic", "organisation_type": "clinic"}
    )
    for clinic in (
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Main Clinic",
            "clinic_type": "clinic",
        },
        {
            "id": "clinic-b",
            "organisation_id": "org-a",
            "name": "Branch Clinic",
            "clinic_type": "branch",
        },
        {
            "id": "clinic-c",
            "organisation_id": "org-b",
            "name": "Unrelated Clinic",
            "clinic_type": "clinic",
        },
    ):
        store.create_clinic(clinic)
    store.create_doctor(
        {
            "id": "doctor-1",
            "email": "doctor@example.test",
            "password_hash": dental_auth.hash_password("correct horse battery staple"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-1", "clinic-a", "owner")
    store.create_doctor_membership("doctor-1", "clinic-b", "dentist")

    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    dental_auth._LOGIN_ATTEMPTS.clear()

    app = FastAPI()
    app.include_router(dental_api.router)

    @app.get("/_test/doctor-context")
    async def doctor_context(doctor: dict = Depends(dental_auth.require_doctor)):
        return doctor

    return TestClient(app), store


def _login(client: TestClient) -> dict:
    response = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "DOCTOR@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_login_returns_authorised_workspaces_and_membership_role(auth_environment):
    client, _store = auth_environment
    body = _login(client)

    assert body["user"]["clinic_id"] == "clinic-a"
    assert body["user"]["role"] == "owner"
    assert [
        (workspace["id"], workspace["role"], workspace["organisation_name"])
        for workspace in body["workspaces"]
    ] == [
        ("clinic-b", "dentist", "Clinic Group"),
        ("clinic-a", "owner", "Clinic Group"),
    ]


def test_workspace_switch_rotates_token_and_revokes_previous_session(auth_environment):
    client, _store = auth_environment
    login = _login(client)
    old_token = login["access_token"]

    response = client.post(
        "/v1/dental/auth/workspace/switch",
        json={"clinic_id": "clinic-b"},
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert response.status_code == 200, response.text
    switched = response.json()
    assert switched["access_token"] != old_token
    assert switched["user"]["clinic_id"] == "clinic-b"
    assert switched["user"]["role"] == "dentist"

    old_request = client.get(
        "/_test/doctor-context",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert old_request.status_code == 401

    new_request = client.get(
        "/_test/doctor-context",
        headers={"Authorization": f"Bearer {switched['access_token']}"},
    )
    assert new_request.status_code == 200
    assert new_request.json()["clinic_id"] == "clinic-b"
    assert new_request.json()["role"] == "dentist"


def test_switch_rejects_workspace_without_membership_and_keeps_current_token(auth_environment):
    client, _store = auth_environment
    login = _login(client)
    token = login["access_token"]

    response = client.post(
        "/v1/dental/auth/workspace/switch",
        json={"clinic_id": "clinic-c"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403

    still_valid = client.get(
        "/_test/doctor-context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert still_valid.status_code == 200
    assert still_valid.json()["clinic_id"] == "clinic-a"


def test_membership_deactivation_denies_existing_session_on_next_request(auth_environment):
    client, store = auth_environment
    login = _login(client)
    store.set_doctor_membership_active("doctor-1", "clinic-a", False)

    response = client.get(
        "/_test/doctor-context",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert response.status_code == 401


def test_single_workspace_login_remains_compatible(auth_environment):
    client, store = auth_environment
    store.create_doctor(
        {
            "id": "doctor-single",
            "email": "single@example.test",
            "password_hash": dental_auth.hash_password("another correct horse battery"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-single", "clinic-a", "doctor")

    response = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "single@example.test",
            "password": "another correct horse battery",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["clinic_id"] == "clinic-a"
    assert len(body["workspaces"]) == 1


def test_bootstrap_admin_receives_owner_membership_in_master_workspace(
    auth_environment, monkeypatch
):
    _client, store = auth_environment
    monkeypatch.setenv("DENTAL_BOOTSTRAP_ADMIN_EMAIL", "bootstrap@example.test")
    monkeypatch.setenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-password-strong")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")

    dental_auth._bootstrap_admin()

    doctor = store.doctor_by_email("bootstrap@example.test")
    assert doctor is not None
    membership = store.doctor_membership(doctor["id"], "clinic-a")
    assert membership is not None
    assert (membership["role"], membership["active"]) == ("owner", 1)
