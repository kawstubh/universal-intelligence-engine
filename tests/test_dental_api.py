from fastapi import FastAPI
from fastapi.testclient import TestClient

from datetime import UTC, datetime, timedelta

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.dental_api import router
from universal_intelligence_engine.dental_store import DentalStore

app = FastAPI()
app.include_router(router)


def _doctor(clinic_id="clinic-a"):
    return {"id": "doctor-1", "clinic_id": clinic_id, "role": "doctor"}


def test_dental_health_is_public(monkeypatch):
    monkeypatch.setenv("DENTAL_API_KEY", "test-key")
    client = TestClient(app)
    response = client.get("/v1/dental/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chart_tooth_validation(monkeypatch):
    app.dependency_overrides[dental_api.require_doctor] = lambda: _doctor()
    client = TestClient(app)
    response = client.post(
        "/v1/dental/patients/p1/chart",
        headers={"Authorization": "Bearer test-key"},
        json={"patient_id": "p1", "tooth_fdi": "99", "status": "Cavity", "note": ""},
    )
    assert response.status_code == 422
    app.dependency_overrides.clear()


def test_public_appointment_intake_does_not_require_doctor_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "dental.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "clinic-public-test")
    monkeypatch.setattr(dental_store, "_store", None)
    store = DentalStore()
    store.create_organisation(
        {"id": "org-public-test", "name": "Synthetic Clinic", "organisation_type": "clinic"}
    )
    store.create_clinic(
        {
            "id": "clinic-public-test",
            "organisation_id": "org-public-test",
            "name": "Synthetic Clinic",
            "clinic_type": "clinic",
        }
    )
    client = TestClient(app)
    starts_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    response = client.post(
        "/v1/dental/public/appointments",
        json={
            "idempotency_key": "dental-api-public-key-0001",
            "name": "Test Patient",
            "phone": "9876543210",
            "starts_at": starts_at,
            "treatment_type": "Dental check-up",
            "intelligence_request": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["reference"].startswith("ORL-")
    assert "appointment" not in body
    assert "patient_id" not in body
    assert store._execute("SELECT COUNT(*) AS n FROM dental_patients")[0]["n"] == 1
    assert store._execute("SELECT COUNT(*) AS n FROM dental_appointments")[0]["n"] == 1


def test_intelligence_denies_cross_clinic_patient(monkeypatch):
    class FakeStore:
        def get_patient(self, patient_id, clinic_id):
            return None

    monkeypatch.setattr(dental_api, "get_dental_store", lambda: FakeStore())
    monkeypatch.setenv("UIE_API_URL", "https://uie.example")
    monkeypatch.setenv("UIE_API_KEY", "test")
    app.dependency_overrides[dental_api.require_doctor] = lambda: _doctor("clinic-a")
    client = TestClient(app)
    response = client.post(
        "/v1/dental/intelligence/run",
        headers={"Authorization": "Bearer test"},
        json={"patient_id": "patient-b", "goal": "Summarize risk flags"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"
    app.dependency_overrides.clear()


def test_intelligence_sends_minimum_patient_context(monkeypatch):
    captured = {}

    class FakeStore:
        def get_patient(self, patient_id, clinic_id):
            return {
                "id": patient_id,
                "name": "Sensitive Name",
                "phone": "9999999999",
                "age": "42",
                "clinic_id": clinic_id,
            }

        def chart(self, patient_id, clinic_id):
            return [{"tooth_fdi": "46", "status": "attention", "note": "private note"}]

        def periodontogram(self, patient_id, clinic_id):
            return [
                {"tooth_fdi": "46", "measurements": {"B": "5"}, "note": "private note"}
            ]

        def appointments(self, clinic_id, patient_id=None):
            return [
                {
                    "starts_at": "2026-10-08",
                    "treatment_type": "RCT",
                    "status": "scheduled",
                    "note": "private",
                }
            ]

        def record_ai(self, *args):
            pass

        def audit(self, *args, **kwargs):
            pass

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"answer": "test", "evidence": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(dental_api, "get_dental_store", lambda: FakeStore())
    monkeypatch.setattr(dental_api.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("UIE_API_URL", "https://uie.example")
    monkeypatch.setenv("UIE_API_KEY", "test")
    app.dependency_overrides[dental_api.require_doctor] = lambda: _doctor("clinic-a")
    client = TestClient(app)
    response = client.post(
        "/v1/dental/intelligence/run",
        headers={"Authorization": "Bearer test"},
        json={
            "patient_id": "patient-a",
            "goal": "Assess risk",
            "context": {"phone": "leak", "free_text": "leak"},
        },
    )
    assert response.status_code == 200
    context = captured["json"]["context"]
    assert context["patient"]["age"] == "42"
    assert "name" not in str(context)
    assert "phone" not in str(context)
    assert "private note" not in str(context)
    assert context["dental_chart"] == [{"tooth_fdi": "46", "status": "attention"}]
    assert captured["json"]["constraints"]["do_not_write_records"] is True
    app.dependency_overrides.clear()


def test_intelligence_provider_not_configured_is_clear(monkeypatch):
    class FakeStore:
        def get_patient(self, patient_id, clinic_id):
            return {"id": patient_id, "age": "42", "clinic_id": clinic_id}

        def chart(self, *args):
            return []

        def periodontogram(self, *args):
            return []

        def appointments(self, *args):
            return []

    monkeypatch.setattr(dental_api, "get_dental_store", lambda: FakeStore())
    monkeypatch.setenv("UIE_API_URL", "https://uie.example")
    monkeypatch.setenv("UIE_API_KEY", "test")

    class FakeResponse:
        status_code = 503

        def json(self):
            return {
                "detail": {
                    "message": "Public knowledge sources are temporarily unavailable.",
                    "code": "PROVIDER_NOT_CONFIGURED",
                    "provider": "knowledge",
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(dental_api.httpx, "AsyncClient", FakeClient)
    app.dependency_overrides[dental_api.require_doctor] = lambda: _doctor("clinic-a")
    response = TestClient(app).post(
        "/v1/dental/intelligence/run",
        headers={"Authorization": "Bearer test"},
        json={"patient_id": "patient-a", "goal": "Assess risk"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
    app.dependency_overrides.clear()
