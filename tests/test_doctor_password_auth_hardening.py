from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app
from universal_intelligence_engine import dental_store


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dental_store._store = None
    return TestClient(app)


def _setup(client, monkeypatch, token="one-time-test-secret"):
    monkeypatch.setenv("DENTAL_SETUP_TOKEN", token)
    return client.post("/v1/dental/auth/bootstrap", json={
        "email": "admin@clinic.example", "password": "first-admin-password-10",
        "display_name": "Clinic Admin", "setup_token": token,
    })


def test_setup_requires_secret_and_is_one_time(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("DENTAL_SETUP_TOKEN", raising=False)
    denied = client.post("/v1/dental/auth/bootstrap", json={
        "email": "stranger@example.com", "password": "untrusted-password-10",
        "display_name": "Stranger", "setup_token": "wrong-secret",
    })
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "SETUP_NOT_AUTHORIZED"
    response = _setup(client, monkeypatch)
    assert response.status_code == 200, response.text
    second = client.post("/v1/dental/auth/bootstrap", json={
        "email": "other@example.com", "password": "another-password-10",
        "display_name": "Other", "setup_token": "one-time-test-secret",
    })
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "SETUP_ALREADY_COMPLETED"


def test_login_unknown_and_wrong_password_same_public_error(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert _setup(client, monkeypatch).status_code == 200
    wrong = client.post("/v1/dental/auth/password", json={
        "email": "admin@clinic.example", "password": "wrong-password",
    })
    unknown = client.post("/v1/dental/auth/password", json={
        "email": "nobody@clinic.example", "password": "wrong-password",
    })
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"]["code"] == unknown.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["detail"]["message"] == unknown.json()["detail"]["message"]


def test_rate_limit_and_audit_have_no_secrets(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert _setup(client, monkeypatch).status_code == 200
    for _ in range(5):
        response = client.post("/v1/dental/auth/password", json={
            "email": "admin@clinic.example", "password": "wrong-password",
        })
        assert response.status_code == 401
    locked = client.post("/v1/dental/auth/password", json={
        "email": "admin@clinic.example", "password": "first-admin-password-10",
    })
    assert locked.status_code == 423
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    db = sqlite3.connect(tmp_path / "auth.sqlite3")
    rows = db.execute("SELECT email,success,ip_address,code FROM dental_login_audit").fetchall()
    throttle = db.execute("SELECT count(*) FROM dental_auth_throttle").fetchone()[0]
    password_hash = db.execute("SELECT password_hash FROM dental_doctors WHERE email='admin@clinic.example'").fetchone()[0]
    db.close()
    assert rows and all("password" not in str(row).lower() and "token" not in str(row).lower() for row in rows)
    assert password_hash.startswith("$2b$12$")
    assert "first-admin-password-10" not in password_hash
    assert throttle >= 2


def test_login_logout_revokes_hashed_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert _setup(client, monkeypatch).status_code == 200
    login = client.post("/v1/dental/auth/password", json={
        "email": "admin@clinic.example", "password": "first-admin-password-10",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]
    db = sqlite3.connect(tmp_path / "auth.sqlite3")
    stored = [row[0] for row in db.execute("SELECT token_hash FROM dental_sessions").fetchall()]
    db.close()
    import hashlib
    assert hashlib.sha256(token.encode()).hexdigest() in stored
    assert token not in stored
    response = client.post("/v1/dental/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    after = client.get("/v1/dental/patients", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 401
    assert after.json()["detail"]["code"] == "SESSION_REVOKED"


def test_password_policy_and_admin_reset_force_change(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    assert setup.status_code == 200
    token = setup.json()["access_token"]
    short = client.post("/v1/dental/auth/password/change", headers={"Authorization": f"Bearer {token}"}, json={
        "current_password": "first-admin-password-10", "new_password": "short",
    })
    assert short.status_code == 422
    created = client.post("/v1/dental/auth/doctors", headers={"Authorization": f"Bearer {token}"}, json={
        "email": "dentist@clinic.example", "display_name": "Associate",
    })
    assert created.status_code == 200, created.text
    assert created.json()["must_change_password"] is True
    assert len(created.json()["temporary_password"]) >= 10
    first_login = client.post("/v1/dental/auth/password", json={
        "email": "dentist@clinic.example", "password": created.json()["temporary_password"],
    })
    assert first_login.status_code == 200
    assert first_login.json()["must_change_password"] is True
    blocked = client.get("/v1/dental/patients", headers={"Authorization": f"Bearer {first_login.json()['access_token']}"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    reset = client.post(f"/v1/dental/auth/doctors/{created.json()['id']}/reset-password", headers={"Authorization": f"Bearer {token}"})
    assert reset.status_code == 200, reset.text
    assert reset.json()["must_change_password"] is True
    reset_login = client.post("/v1/dental/auth/password", json={
        "email": "dentist@clinic.example", "password": reset.json()["temporary_password"],
    })
    assert reset_login.status_code == 200
    changed = client.post("/v1/dental/auth/password/change", headers={"Authorization": f"Bearer {reset_login.json()['access_token']}"}, json={
        "current_password": reset.json()["temporary_password"], "new_password": "dentist-new-password-10",
    })
    assert changed.status_code == 200
    final_login = client.post("/v1/dental/auth/password", json={
        "email": "dentist@clinic.example", "password": "dentist-new-password-10",
    })
    assert final_login.status_code == 200


def test_cross_clinic_admin_cannot_reset_other_clinic_doctor(tmp_path, monkeypatch):
    from universal_intelligence_engine.dental_auth import reset_doctor_password, hash_password
    from universal_intelligence_engine.dental_store import get_dental_store
    from fastapi import HTTPException
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    assert setup.status_code == 200
    actor = {"id": setup.json()["user"]["id"], "clinic_id": "dr-pranali", "role": "admin"}
    store = get_dental_store()
    store._execute(
        "INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("foreign-doctor-id", "foreign@other-clinic.example", "", hash_password("foreign-doctor-password"),
         "doctor", "clinic-b", "1", True, False, "Foreign Doctor"),
    )
    try:
        reset_doctor_password(actor, "foreign-doctor-id")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-clinic reset unexpectedly succeeded")



def test_expired_session_has_stable_code(tmp_path, monkeypatch):
    import hashlib
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    assert setup.status_code == 200
    token = setup.json()["access_token"]
    db = sqlite3.connect(tmp_path / "auth.sqlite3")
    db.execute("UPDATE dental_sessions SET expires_at='1' WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    db.commit(); db.close()
    response = client.get("/v1/dental/patients", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "SESSION_EXPIRED"


def test_password_change_revokes_all_sessions_and_new_password_works(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    assert setup.status_code == 200
    old_token = setup.json()["access_token"]
    login = client.post("/v1/dental/auth/password", json={"email":"admin@clinic.example","password":"first-admin-password-10"})
    assert login.status_code == 200
    second_token = login.json()["access_token"]
    changed = client.post("/v1/dental/auth/password/change", headers={"Authorization":f"Bearer {old_token}"}, json={
        "current_password":"first-admin-password-10","new_password":"new-admin-password-11",
    })
    assert changed.status_code == 200, changed.text
    for token in (old_token, second_token):
        response = client.get("/v1/dental/patients", headers={"Authorization":f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "SESSION_REVOKED"
    new_login = client.post("/v1/dental/auth/password", json={"email":"admin@clinic.example","password":"new-admin-password-11"})
    assert new_login.status_code == 200


def test_email_rate_limit_applies_across_ips(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from universal_intelligence_engine.dental_auth import login_password
    client = _client(tmp_path, monkeypatch)
    assert _setup(client, monkeypatch).status_code == 200
    for i in range(5):
        try:
            login_password("admin@clinic.example","bad-password",f"192.0.2.{i+1}")
        except HTTPException as exc:
            assert exc.status_code == 401
    try:
        login_password("admin@clinic.example","first-admin-password-10","192.0.2.99")
    except HTTPException as exc:
        assert exc.status_code == 423
        assert exc.detail["code"] == "ACCOUNT_LOCKED"
    else:
        raise AssertionError("email-level lockout was not enforced")


def test_ip_rate_limit_applies_across_emails(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from universal_intelligence_engine.dental_auth import login_password
    client = _client(tmp_path, monkeypatch)
    assert _setup(client, monkeypatch).status_code == 200
    for i in range(5):
        try:
            login_password(f"unknown{i}@clinic.example","bad-password","198.51.100.44")
        except HTTPException as exc:
            assert exc.status_code == 401
    try:
        login_password("admin@clinic.example","first-admin-password-10","198.51.100.44")
    except HTTPException as exc:
        assert exc.status_code == 423
        assert exc.detail["code"] == "ACCOUNT_LOCKED"
    else:
        raise AssertionError("IP-level lockout was not enforced")


def test_admin_deactivation_revokes_doctor_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    admin_token = setup.json()["access_token"]
    cannot_disable_admin = client.patch(f"/v1/dental/auth/doctors/{setup.json()['user']['id']}/deactivate", headers={"Authorization":f"Bearer {admin_token}"})
    assert cannot_disable_admin.status_code == 409
    created = client.post("/v1/dental/auth/doctors", headers={"Authorization":f"Bearer {admin_token}"}, json={
        "email":"associate@clinic.example","display_name":"Associate",
    })
    assert created.status_code == 200
    doctor_login = client.post("/v1/dental/auth/password", json={
        "email":"associate@clinic.example","password":created.json()["temporary_password"],
    })
    assert doctor_login.status_code == 200
    doctor_token = doctor_login.json()["access_token"]
    deactivated = client.patch(f"/v1/dental/auth/doctors/{created.json()['id']}/deactivate", headers={"Authorization":f"Bearer {admin_token}"})
    assert deactivated.status_code == 200
    denied = client.get("/v1/dental/patients", headers={"Authorization":f"Bearer {doctor_token}"})
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "SESSION_REVOKED"



def test_public_clinic_profile_returns_record_phone_without_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/v1/dental/public/clinic")
    assert response.status_code == 200, response.text
    clinic = response.json()["clinic"]
    assert clinic["phone"] == "9137007432"
    assert clinic["phone_e164"] == "+919137007432"
    assert clinic["whatsapp_phone"] == "919137007432"



def test_legacy_scrypt_password_is_upgraded_to_bcrypt_after_successful_login(tmp_path, monkeypatch):
    import hashlib
    import secrets
    client = _client(tmp_path, monkeypatch)
    setup = _setup(client, monkeypatch)
    assert setup.status_code == 200
    password = "legacy-doctor-password-10"
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    legacy = "scrypt$" + salt.hex() + "$" + digest.hex()
    db = sqlite3.connect(tmp_path / "auth.sqlite3")
    db.execute("INSERT INTO dental_doctors(id,email,phone,password_hash,role,clinic_id,created_at,is_active,must_change_password,display_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("legacy-doctor", "legacy@clinic.example", "", legacy, "doctor", "dr-pranali", "1", 1, 0, "Legacy Doctor"))
    db.commit(); db.close()
    response = client.post("/v1/dental/auth/password", json={"email":"legacy@clinic.example","password":password})
    assert response.status_code == 200, response.text
    db = sqlite3.connect(tmp_path / "auth.sqlite3")
    upgraded = db.execute("SELECT password_hash FROM dental_doctors WHERE id='legacy-doctor'").fetchone()[0]
    db.close()
    assert upgraded.startswith("$2b$12$")
