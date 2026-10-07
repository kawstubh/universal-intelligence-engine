from fastapi.testclient import TestClient
from fastapi import FastAPI

from universal_intelligence_engine.dental_api import router, run_dental_intelligence
from universal_intelligence_engine import dental_api

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
    dental_api._store = None
    client = TestClient(app)
    response = client.post(
        "/v1/dental/public/appointments",
        json={
            "name": "Test Patient",
            "phone": "9876543210",
            "starts_at": "2026-10-06 10:00 AM",
            "treatment_type": "Dental Check-up",
            "note": None,
            "intelligence_request": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requested"
    assert body["appointment"]["status"] == "requested"
    assert body["appointment"]["treatment_type"] == "Dental Check-up"


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
            return {"id": patient_id, "name": "Sensitive Name", "phone": "9999999999", "age": "42", "clinic_id": clinic_id}
        def chart(self, patient_id, clinic_id):
            return [{"tooth_fdi": "46", "status": "attention", "note": "private note"}]
        def periodontogram(self, patient_id, clinic_id):
            return [{"tooth_fdi": "46", "measurements": {"B": "5"}, "note": "private note"}]
        def appointments(self, clinic_id, patient_id=None):
            return [{"starts_at": "2026-10-08", "treatment_type": "RCT", "status": "scheduled", "note": "private"}]
        def record_ai(self, *args):
            pass
        def audit(self, *args):
            pass

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"answer": "test", "evidence": []}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
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
        json={"patient_id": "patient-a", "goal": "Assess risk", "context": {"phone": "leak", "free_text": "leak"}},
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
        def chart(self, *args): return []
        def periodontogram(self, *args): return []
        def appointments(self, *args): return []
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: FakeStore())
    monkeypatch.setenv("UIE_API_URL", "https://uie.example")
    monkeypatch.setenv("UIE_API_KEY", "test")
    class FakeResponse:
        status_code = 503
        def json(self):
            return {"detail": {"message": "Knowledge provider is not configured: BRAVE_SEARCH_API_KEY is missing", "code": "PROVIDER_NOT_CONFIGURED", "provider": "knowledge"}}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return FakeResponse()
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
