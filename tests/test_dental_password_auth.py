from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_auth
from universal_intelligence_engine.dental_api import router


app = FastAPI()
app.include_router(router)


def test_password_hash_is_salted_and_verifiable():
    first = dental_auth.hash_password("correct horse battery staple")
    second = dental_auth.hash_password("correct horse battery staple")
    assert first != second
    assert dental_auth.verify_password("correct horse battery staple", first)
    assert not dental_auth.verify_password("wrong password", first)


def test_password_login_issues_session_for_active_doctor(monkeypatch):
    password_hash = dental_auth.hash_password("correct horse battery staple")

    class FakeStore:
        created = None

        def doctor_by_email(self, email):
            assert email == "doctor@example.com"
            return {"id": "doctor-1", "email": email, "password_hash": password_hash,
                    "role": "doctor", "clinic_id": "clinic-a", "active": True}

        def create_session(self, doctor_id, token_hash, expires_at):
            self.created = (doctor_id, token_hash, expires_at)

    store = FakeStore()
    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_auth, "_bootstrap_admin", lambda: None)
    dental_auth._LOGIN_ATTEMPTS.clear()
    response = TestClient(app).post(
        "/v1/dental/auth/password/login",
        json={"email": "DOCTOR@example.com", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "doctor"
    assert body["user"]["clinic_id"] == "clinic-a"
    assert body["access_token"]
    assert store.created[0] == "doctor-1|clinic-a|doctor"
    assert body["access_token"] not in store.created[1]


def test_password_login_rejects_wrong_password(monkeypatch):
    password_hash = dental_auth.hash_password("correct horse battery staple")

    class FakeStore:
        def doctor_by_email(self, email):
            return {"id": "doctor-1", "email": email, "password_hash": password_hash,
                    "role": "doctor", "clinic_id": "clinic-a", "active": True}

    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: FakeStore())
    monkeypatch.setattr(dental_auth, "_bootstrap_admin", lambda: None)
    dental_auth._LOGIN_ATTEMPTS.clear()
    response = TestClient(app).post(
        "/v1/dental/auth/password/login",
        json={"email": "doctor@example.com", "password": "wrong password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_google_login_route_is_removed():
    paths = set(app.openapi()["paths"])
    assert "/v1/dental/auth/google" not in paths
    assert "/v1/dental/auth/password/login" in paths


def test_bootstrap_admin_does_not_recreate_existing_admin(monkeypatch):
    """First-run bootstrap is idempotent once the configured admin exists."""
    from universal_intelligence_engine import dental_auth

    class ExistingAdminStore:
        def __init__(self):
            self.created = []

        def doctor_by_email(self, email):
            return {"id": "existing-admin", "email": email, "role": "admin", "clinic_id": "clinic-a"}

        def create_doctor(self, payload):
            self.created.append(payload)

    store = ExistingAdminStore()
    monkeypatch.setenv("DENTAL_BOOTSTRAP_ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "a-strong-test-password")
    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    dental_auth._bootstrap_admin()
    assert store.created == []
