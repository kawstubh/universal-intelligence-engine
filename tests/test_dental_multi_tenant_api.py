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
    assert body["workspace"]["id"] in {
        workspace["id"] for workspace in store.workspaces_for_doctor("doctor-admin")
    }
    assert body["organisation"]["id"] in {
        organisation["id"]
        for organisation in store.organisations_for_doctor("doctor-admin")
    }
    created_workspaces = store.workspaces_for_organisation(body["organisation"]["id"])
    assert [
        (workspace["id"], workspace["active_doctors"])
        for workspace in created_workspaces
    ] == [(body["workspace"]["id"], 1)]


def test_audited_organisation_creator_can_join_another_workspace_in_their_organisation(
    tenant_api, admin_headers
):
    client, store = tenant_api
    created = client.post(
        "/v1/dental/organisations",
        headers=admin_headers,
        json={
            "name": "Creator-Owned Hospital Group",
            "organisation_type": "hospital",
            "workspace_name": "First Department",
            "clinic_type": "hospital_department",
        },
    )
    assert created.status_code == 201, created.text
    organisation_id = created.json()["organisation"]["id"]
    first_workspace_id = created.json()["workspace"]["id"]

    store.create_doctor(
        {
            "id": "second-org-owner",
            "email": "second-org-owner@example.test",
            "password_hash": dental_auth.hash_password("second org owner password"),
            "role": "doctor",
            "clinic_id": first_workspace_id,
        }
    )
    store.create_doctor_membership("second-org-owner", first_workspace_id, "owner")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "second-org-owner@example.test",
            "password": "second org owner password",
        },
    )
    assert login.status_code == 200, login.text
    owner_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    second_workspace = client.post(
        f"/v1/dental/organisations/{organisation_id}/workspaces",
        headers=owner_headers,
        json={"name": "Second Department", "clinic_type": "hospital_department"},
    )
    assert second_workspace.status_code == 201, second_workspace.text

    assigned = client.post(
        f"/v1/dental/workspaces/{second_workspace.json()['id']}/memberships",
        headers=owner_headers,
        json={"doctor_id": "doctor-admin", "role": "owner"},
    )
    assert assigned.status_code == 201, assigned.text
    creator_membership = store.doctor_membership(
        "doctor-admin", second_workspace.json()["id"]
    )
    assert creator_membership is not None
    assert creator_membership["role"] == "owner"
    assert creator_membership["active"] in (True, 1)


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


def test_admin_can_list_workspaces_only_inside_their_organisation(tenant_api):
    client, store = tenant_api
    store.create_doctor_membership("doctor-target", "clinic-a", "admin")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "target@example.test",
            "password": "another correct horse battery",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get(
        "/v1/dental/organisations/org-a/workspaces", headers=headers
    )
    assert response.status_code == 200, response.text
    assert {workspace["id"] for workspace in response.json()["workspaces"]} == {
        "clinic-a",
        "clinic-b",
    }

    denied = client.get(
        "/v1/dental/organisations/org-b/workspaces", headers=headers
    )
    assert denied.status_code == 403

def test_admin_cannot_assign_or_manage_doctor_from_another_organisation(tenant_api):
    client, store = tenant_api
    store.create_doctor(
        {
            "id": "hospital-admin",
            "email": "hospital-admin@example.test",
            "password_hash": dental_auth.hash_password("hospital admin password"),
            "role": "doctor",
            "clinic_id": "clinic-c",
        }
    )
    store.create_doctor_membership("hospital-admin", "clinic-c", "admin")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "hospital-admin@example.test",
            "password": "hospital admin password",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assignment = client.post(
        "/v1/dental/workspaces/clinic-c/memberships",
        headers=headers,
        json={"doctor_id": "doctor-target", "role": "dentist"},
    )
    assert assignment.status_code == 403
    assert assignment.json()["detail"]["code"] == "DOCTOR_ORGANISATION_MISMATCH"

    # Also protect pre-existing/corrupt cross-tenant membership rows from
    # being administered through the target organisation's workspace.
    store.create_doctor_membership("doctor-target", "clinic-c", "doctor")
    update = client.patch(
        "/v1/dental/workspaces/clinic-c/memberships/doctor-target",
        headers=headers,
        json={"active": False},
    )
    assert update.status_code == 403
    assert update.json()["detail"]["code"] == "DOCTOR_ORGANISATION_MISMATCH"


def test_corrupt_cross_organisation_admin_membership_cannot_authenticate_or_access_directory(
    tenant_api,
):
    client, store = tenant_api
    store.create_doctor_membership("doctor-target", "clinic-a", "doctor")
    store.create_doctor_membership("doctor-target", "clinic-c", "admin")

    # The doctor's identity is anchored to clinic-a/org-a. A manually inserted
    # admin membership in clinic-c/org-b must not appear in login or workspace APIs.
    assert [w["id"] for w in store.workspaces_for_doctor("doctor-target")] == [
        "clinic-a"
    ]
    assert [o["id"] for o in store.organisations_for_doctor("doctor-target")] == [
        "org-a"
    ]
    partner_workspaces = store.workspaces_for_organisation("org-b")
    assert [
        (workspace["id"], workspace["active_doctors"])
        for workspace in partner_workspaces
    ] == [("clinic-c", 0)]

    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "target@example.test",
            "password": "another correct horse battery",
        },
    )
    assert login.status_code == 200, login.text
    assert [w["id"] for w in login.json()["workspaces"]] == ["clinic-a"]
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    denied = client.get("/v1/dental/organisations/org-b/workspaces", headers=headers)
    assert denied.status_code == 403

    # Also reject a previously issued session whose workspace context points at
    # the corrupted cross-organisation membership.
    stale_token = "corrupt-cross-organisation-admin-session-token"
    store.create_session(
        "doctor-target|clinic-c|admin",
        dental_auth._hash(stale_token),
        4102444800,
    )
    stale = client.get(
        "/v1/dental/organisations/org-b/workspaces",
        headers={"Authorization": f"Bearer {stale_token}"},
    )
    assert stale.status_code == 401
    assert stale.json()["detail"]["code"] == "WORKSPACE_ACCESS_REVOKED"




def test_corrupt_cross_organisation_owner_membership_requires_creation_audit(
    tenant_api,
):
    client, store = tenant_api
    store.create_doctor_membership("doctor-target", "clinic-a", "doctor")
    store.create_doctor_membership("doctor-target", "clinic-c", "owner")

    assert [w["id"] for w in store.workspaces_for_doctor("doctor-target")] == [
        "clinic-a"
    ]
    assert [o["id"] for o in store.organisations_for_doctor("doctor-target")] == [
        "org-a"
    ]
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "target@example.test",
            "password": "another correct horse battery",
        },
    )
    assert login.status_code == 200, login.text
    assert [w["id"] for w in login.json()["workspaces"]] == ["clinic-a"]


def test_admin_creator_is_added_to_new_workspace_membership(tenant_api):
    client, store = tenant_api
    store.create_doctor(
        {
            "id": "clinic-admin",
            "email": "clinic-admin@example.test",
            "password_hash": dental_auth.hash_password("clinic admin password"),
            "role": "doctor",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("clinic-admin", "clinic-a", "admin")
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "clinic-admin@example.test",
            "password": "clinic admin password",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = client.post(
        "/v1/dental/organisations/org-a/workspaces",
        headers=headers,
        json={"name": "Admin-created branch", "clinic_type": "branch"},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    membership = store.doctor_membership("clinic-admin", workspace_id)
    assert membership is not None
    assert membership["role"] == "admin"
    assert membership["active"] in (True, 1)

    authorised = client.get("/v1/dental/auth/workspaces", headers=headers)
    assert authorised.status_code == 200
    assert workspace_id in {
        workspace["id"] for workspace in authorised.json()["workspaces"]
    }

def test_active_doctor_counts_exclude_inactive_doctors_and_memberships(tenant_api, admin_headers):
    client, store = tenant_api
    store.create_doctor_membership("doctor-target", "clinic-a", "dentist")
    store.create_doctor_membership("doctor-admin", "clinic-b", "dentist")
    store.set_doctor_membership_active("doctor-admin", "clinic-b", False)
    store._execute("UPDATE dental_doctors SET active=0 WHERE id=?", ("doctor-target",))

    response = client.get(
        "/v1/dental/organisations/org-a/workspaces", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    counts = {
        workspace["id"]: workspace["active_doctors"]
        for workspace in response.json()["workspaces"]
    }
    # The owner in clinic-a remains active; an inactive doctor and an inactive
    # membership must not contribute to their respective workspace counts.
    assert counts == {"clinic-a": 1, "clinic-b": 0}



def test_active_doctor_counts_exclude_administrative_accounts(
    tenant_api, admin_headers
):
    client, store = tenant_api
    store.create_doctor(
        {
            "id": "doctor-admin-account",
            "email": "staff-admin@example.test",
            "password_hash": "test-hash",
            "role": "admin",
            "clinic_id": "clinic-a",
        }
    )
    store.create_doctor_membership("doctor-admin-account", "clinic-a", "admin")

    response = client.get(
        "/v1/dental/organisations/org-a/workspaces", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    counts = {row["id"]: row["active_doctors"] for row in response.json()["workspaces"]}
    assert counts == {"clinic-a": 1, "clinic-b": 0}



def test_organisation_creation_rolls_back_if_creator_membership_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    original_create_membership = store.create_doctor_membership

    def fail_after_membership_insert(doctor_id, clinic_id, role, connection=None):
        original_create_membership(
            doctor_id, clinic_id, role, connection=connection
        )
        raise RuntimeError("simulated creator membership failure")

    monkeypatch.setattr(
        store, "create_doctor_membership", fail_after_membership_insert
    )

    with pytest.raises(RuntimeError, match="simulated creator membership failure"):
        client.post(
            "/v1/dental/organisations",
            headers=admin_headers,
            json={
                "name": "Rollback Network",
                "organisation_type": "group",
                "workspace_name": "Rollback First Workspace",
                "clinic_type": "clinic",
            },
        )

    assert store._execute(
        "SELECT id FROM dental_organisations WHERE name=?",
        ("Rollback Network",),
    ) == []
    assert store._execute(
        "SELECT id FROM dental_clinics WHERE name=?",
        ("Rollback First Workspace",),
    ) == []


def test_workspace_creation_rolls_back_if_audit_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    original_audit = store.audit

    def fail_after_audit_insert(
        actor_id, action, resource_type, resource_id=None, clinic_id=None, connection=None
    ):
        original_audit(
            actor_id,
            action,
            resource_type,
            resource_id,
            clinic_id=clinic_id,
            connection=connection,
        )
        raise RuntimeError("simulated workspace audit failure")

    monkeypatch.setattr(store, "audit", fail_after_audit_insert)

    with pytest.raises(RuntimeError, match="simulated workspace audit failure"):
        client.post(
            "/v1/dental/organisations/org-a/workspaces",
            headers=admin_headers,
            json={"name": "Rollback Branch", "clinic_type": "branch"},
        )

    assert store._execute(
        "SELECT id FROM dental_clinics WHERE name=?",
        ("Rollback Branch",),
    ) == []
    assert store._execute(
        "SELECT id FROM dental_doctor_memberships WHERE clinic_id IN "
        "(SELECT id FROM dental_clinics WHERE name=?)",
        ("Rollback Branch",),
    ) == []


def test_doctor_account_creation_rolls_back_if_membership_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    original_create_membership = store.create_doctor_membership

    def fail_after_membership_insert(doctor_id, clinic_id, role, connection=None):
        original_create_membership(
            doctor_id, clinic_id, role, connection=connection
        )
        raise RuntimeError("simulated doctor membership failure")

    monkeypatch.setattr(
        store, "create_doctor_membership", fail_after_membership_insert
    )

    with pytest.raises(RuntimeError, match="simulated doctor membership failure"):
        client.post(
            "/v1/dental/auth/doctors",
            headers=admin_headers,
            json={
                "email": "rollback-doctor@example.test",
                "password": "rollback-doctor-password",
                "role": "dentist",
            },
        )

    assert store.doctor_by_email("rollback-doctor@example.test") is None

def test_workspace_membership_creation_rolls_back_if_audit_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    original_audit = store.audit
    attempted_resource_ids = []

    def fail_after_audit_insert(
        actor_id, action, resource_type, resource_id=None, clinic_id=None, connection=None
    ):
        attempted_resource_ids.append(resource_id)
        original_audit(
            actor_id,
            action,
            resource_type,
            resource_id,
            clinic_id=clinic_id,
            connection=connection,
        )
        raise RuntimeError("simulated membership-create audit failure")

    monkeypatch.setattr(store, "audit", fail_after_audit_insert)

    with pytest.raises(RuntimeError, match="membership-create audit failure"):
        client.post(
            "/v1/dental/workspaces/clinic-b/memberships",
            headers=admin_headers,
            json={"doctor_id": "doctor-target", "role": "dentist"},
        )

    assert store.doctor_membership("doctor-target", "clinic-b") is None
    assert attempted_resource_ids
    assert store._execute(
        "SELECT id FROM dental_audit_events WHERE resource_id=?",
        (attempted_resource_ids[0],),
    ) == []


def test_workspace_membership_update_rolls_back_if_audit_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    membership = store.create_doctor_membership(
        "doctor-target", "clinic-b", "dentist"
    )
    original_audit = store.audit

    def fail_after_audit_insert(
        actor_id, action, resource_type, resource_id=None, clinic_id=None, connection=None
    ):
        original_audit(
            actor_id,
            action,
            resource_type,
            resource_id,
            clinic_id=clinic_id,
            connection=connection,
        )
        raise RuntimeError("simulated membership-update audit failure")

    monkeypatch.setattr(store, "audit", fail_after_audit_insert)

    with pytest.raises(RuntimeError, match="membership-update audit failure"):
        client.patch(
            "/v1/dental/workspaces/clinic-b/memberships/doctor-target",
            headers=admin_headers,
            json={"active": False},
        )

    persisted = store.doctor_membership("doctor-target", "clinic-b")
    assert persisted is not None
    assert persisted["active"] in (True, 1)
    assert store._execute(
        "SELECT id FROM dental_audit_events WHERE resource_id=? AND action=?",
        (membership["id"], "membership.deactivated"),
    ) == []


def test_doctor_deactivation_rolls_back_if_audit_fails(
    tenant_api, admin_headers, monkeypatch
):
    client, store = tenant_api
    membership = store.create_doctor_membership(
        "doctor-target", "clinic-a", "dentist"
    )
    original_audit = store.audit

    def fail_after_audit_insert(
        actor_id, action, resource_type, resource_id=None, clinic_id=None, connection=None
    ):
        original_audit(
            actor_id,
            action,
            resource_type,
            resource_id,
            clinic_id=clinic_id,
            connection=connection,
        )
        raise RuntimeError("simulated doctor-deactivation audit failure")

    monkeypatch.setattr(store, "audit", fail_after_audit_insert)

    with pytest.raises(RuntimeError, match="doctor-deactivation audit failure"):
        client.post(
            "/v1/dental/auth/doctors/doctor-target/deactivate",
            headers=admin_headers,
        )

    persisted = store.doctor_membership("doctor-target", "clinic-a")
    assert persisted is not None
    assert persisted["active"] in (True, 1)
    assert store._execute(
        "SELECT id FROM dental_audit_events WHERE resource_id=? AND action=?",
        (membership["id"], "membership.deactivated"),
    ) == []


def test_doctor_deactivation_rejects_corrupt_cross_organisation_membership(
    tenant_api
):
    client, store = tenant_api
    store.create_doctor(
        {
            "id": "hospital-admin",
            "email": "hospital-admin@example.test",
            "password_hash": dental_auth.hash_password("hospital admin password"),
            "role": "doctor",
            "clinic_id": "clinic-c",
        }
    )
    store.create_doctor_membership("hospital-admin", "clinic-c", "admin")
    corrupt_membership = store.create_doctor_membership(
        "doctor-target", "clinic-c", "dentist"
    )
    login = client.post(
        "/v1/dental/auth/password/login",
        json={
            "email": "hospital-admin@example.test",
            "password": "hospital admin password",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(
        "/v1/dental/auth/doctors/doctor-target/deactivate",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DOCTOR_ORGANISATION_MISMATCH"
    persisted = store.doctor_membership("doctor-target", "clinic-c")
    assert persisted is not None
    assert persisted["id"] == corrupt_membership["id"]
    assert persisted["active"] in (True, 1)

