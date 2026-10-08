import pytest
from fastapi.testclient import TestClient

from universal_intelligence_engine.api import app
from universal_intelligence_engine import dental_ai, dental_api, dental_store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "patient-link.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-a")
    dental_api._store = None
    dental_store._store = None
    yield dental_api.get_dental_store()
    app.dependency_overrides.clear()
    dental_api._store = None
    dental_store._store = None


def test_phone_alone_cannot_claim_patient_record(db):
    patient_a = db.create_patient({"name": "A", "phone": "9000000001"}, "clinic-a")
    patient_b = db.create_patient({"name": "B", "phone": "9000000002"}, "clinic-a")
    app.dependency_overrides[dental_ai.require_patient] = lambda: {"user_id": "user-a", "email": "a@example.com"}
    client = TestClient(app)

    response = client.post("/v1/dental/ai/patient/link", json={"phone": patient_b["phone"]})
    assert response.status_code == 422

    app.dependency_overrides.clear()
    assert db.patient_by_user_id("user-a") is None
    assert db.get_patient(patient_b["id"], "clinic-a")["user_id"] is None


def test_user_a_cannot_link_or_read_user_b_record_with_b_phone(db):
    patient_a = db.create_patient({"name": "A", "phone": "9000000001"}, "clinic-a")
    patient_b = db.create_patient({"name": "B", "phone": "9000000002"}, "clinic-a")
    code_b = db.create_patient_invite(patient_b["id"], "clinic-a")

    assert db.consume_patient_invite(code_b, "user-b")["id"] == patient_b["id"]

    app.dependency_overrides[dental_ai.require_patient] = lambda: {"user_id": "user-a", "email": "a@example.com"}
    client = TestClient(app)
    appointments = client.get("/v1/dental/ai/patient/appointments")
    chart = client.get("/v1/dental/ai/patient/chart")
    assert appointments.json()["result"] == []
    assert chart.json()["result"] == []

    response = client.post("/v1/dental/ai/patient/link", json={"invite_code": code_b})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PATIENT_INVITE_INVALID"

    assert db.patient_by_user_id("user-a") is None
    assert db.patient_by_user_id("user-b", patient_b["id"])["id"] == patient_b["id"]
    assert db.patient_by_user_id("user-a", patient_b["id"]) is None


def test_clinic_issued_invite_links_only_once_and_expires(db, monkeypatch):
    patient = db.create_patient({"name": "Invite Patient", "phone": "9000000003"}, "clinic-a")
    code = db.create_patient_invite(patient["id"], "clinic-a", ttl_seconds=60)
    app.dependency_overrides[dental_ai.require_patient] = lambda: {"user_id": "user-a", "email": "a@example.com"}
    client = TestClient(app)
    response = client.post("/v1/dental/ai/patient/link", json={"invite_code": code})
    assert response.status_code == 200
    assert response.json()["result"]["patient_id"] == patient["id"]

    app.dependency_overrides[dental_ai.require_patient] = lambda: {"user_id": "user-b", "email": "b@example.com"}
    second = client.post("/v1/dental/ai/patient/link", json={"invite_code": code})
    assert second.status_code == 403
