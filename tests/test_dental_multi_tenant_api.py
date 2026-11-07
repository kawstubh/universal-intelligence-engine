from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_auth
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def tenant_api(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "multi-tenant-api.sqlite3"
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
        {"id": "org-b", "name": "Other Hospital", "organisation_type": "hospital"}
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
            "name": "Hospital Department",
            "clinic_type": "hospital_department",
        },
    ):
        store.create_clinic(clinic)
    store.create_doctor(
        {
            "id": "doctor-admin",
            "email": "admin@example.test",
            "password_hash": dental_auth.hash_password("correct horse battery staple"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-admin", "clinic-a", "owner")
    store.create_doctor(
        {
            "id": "doctor-target",
            "email": "target@example.test",
            "password_hash": dental_auth.hash_password("another correct horse battery"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )

    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    dental_auth._LOGIN_ATTEMPTS.clear()
    app = FastAPI()
    app.include_router(dental_api.router)
    client = TestClient(app)
    return client, store


@pytest.fixture
def admin_headers(tenant_api):
    client, _store = tenant_api
    response = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_authenticated_doctor_lists_only_authorised_workspaces(tenant_api, admin_headers):
    client, _store = tenant_api
    response = client.get("/v1/dental/auth/workspaces", headers=admin_headers)
    assert response.status_code == 200
    assert [
        (workspace["id"], workspace["role"])
        for workspace in response.json()["workspaces"]
    ] == [("clinic-a", "owner")]


def test_owner_can_create_organisation_and_first_workspace(tenant_api, admin_headers):
    client, store = tenant_api
    response = client.post(
        "/v1/dental/organisations",
        headers=admin_headers,
        json={
            "name": "New Dental Network",
            "organisation_type": "group",
            "workspace_name": "First Branch",
            "clinic_type": "clinic",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organisation"]["name"] == "New Dental Network"
    assert body["workspace"]["name"] == "First Branch"
    membership = store.doctor_membership("doctor-admin", body["workspace"]["id"])
    assert membership is not None
    assert (membership["role"], membership["active"]) == ("owner", 1)


def test_owner_can_create_workspace_only_inside_own_organisation(
    tenant_api, admin_headers
):
    client, store = tenant_api
    created = client.post(
        "/v1/dental/organisations/org-a/workspaces",
        headers=admin_headers,
        json={"name": "Second Branch", "clinic_type": "branch"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["organisation_id"] == "org-a"
    membership = store.doctor_membership("doctor-admin", created.json()["id"])
    assert membership is not None
    assert membership["role"] == "owner"
    assert membership["active"] in (True, 1)
    authorised = client.get("/v1/dental/auth/workspaces", headers=admin_headers)
    assert created.json()["id"] in {
        workspace["id"] for workspace in authorised.json()["workspaces"]
    }

    denied = client.post(
        "/v1/dental/organisations/org-b/workspaces",
        headers=admin_headers,
        json={"name": "Forbidden Branch", "clinic_type": "branch"},
    )
    assert denied.status_code == 403


def test_owner_can_list_workspaces_only_inside_own_organisation(
    tenant_api, admin_headers
):
    client, _store = tenant_api
    response = client.get(
        "/v1/dental/organisations/org-a/workspaces", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    workspaces = response.json()["workspaces"]
    assert {workspace["id"] for workspace in workspaces} == {"clinic-a", "clinic-b"}
    counts = {workspace["id"]: workspace["active_doctors"] for workspace in workspaces}
    assert counts == {"clinic-a": 1, "clinic-b": 0}
    assert {workspace["organisation_type"] for workspace in workspaces} == {"group"}

    denied = client.get(
        "/v1/dental/organisations/org-b/workspaces", headers=admin_headers
    )
    assert denied.status_code == 403


def test_owner_can_assign_and_deactivate_membership_within_organisation(
    tenant_api, admin_headers
):
    client, store = tenant_api
    assigned = client.post(
        "/v1/dental/workspaces/clinic-b/memberships",
        headers=admin_headers,
        json={"doctor_id": "doctor-target", "role": "dentist"},
    )
    assert assigned.status_code == 201, assigned.text
    membership = store.doctor_membership("doctor-target", "clinic-b")
    assert membership is not None
    assert membership["role"] == "dentist"

    deactivated = client.patch(
        "/v1/dental/workspaces/clinic-b/memberships/doctor-target",
        headers=admin_headers,
        json={"active": False},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert store.doctor_membership("doctor-target", "clinic-b")["active"] == 0

    cross_org = client.post(
        "/v1/dental/workspaces/clinic-c/memberships",
        headers=admin_headers,
        json={"doctor_id": "doctor-target", "role": "dentist"},
    )
    assert cross_org.status_code == 403


def test_doctor_account_creation_adds_membership_and_deactivation_is_workspace_scoped(
    tenant_api, admin_headers
):
    client, store = tenant_api
    created = client.post(
        "/v1/dental/auth/doctors",
        headers=admin_headers,
        json={
            "email": "new-doctor@example.test",
            "password": "new-doctor-password-strong",
            "role": "dentist",
        },
    )
    assert created.status_code == 200, created.text
    doctor_id = created.json()["id"]
    assert store.doctor_membership(doctor_id, "clinic-a")["role"] == "dentist"

    deactivated = client.post(
        f"/v1/dental/auth/doctors/{doctor_id}/deactivate",
        headers=admin_headers,
    )
    assert deactivated.status_code == 200
    assert store.doctor_by_id(doctor_id)["active"] in (True, 1)
    assert store.doctor_membership(doctor_id, "clinic-a")["active"] == 0


def test_non_admin_cannot_create_organisation(tenant_api):
    client, store = tenant_api
    store.create_doctor_membership("doctor-target", "clinic-a", "doctor")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "target@example.test",
            "password": "another correct horse battery",
        },
    )
    token = login.json()["access_token"]
    response = client.post(
        "/v1/dental/organisations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Not Allowed", "organisation_type": "clinic"},
    )
    assert response.status_code == 403
    workspace_list = client.get(
        "/v1/dental/organisations/org-a/workspaces",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert workspace_list.status_code == 403
