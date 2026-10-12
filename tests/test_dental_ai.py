from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine.dental_ai import router
from universal_intelligence_engine import dental_ai

app = FastAPI()
app.include_router(router)


def doctor():
    return {"id": "doctor-1", "clinic_id": "clinic-a", "role": "doctor"}


def patient():
    return {"user_id": "patient-user", "email": "patient@example.com"}


def test_doctor_cross_clinic_denied(monkeypatch):
    class Store:
        def ai_usage_count(self, *args, **kwargs): return 0
        def get_patient(self, patient_id, clinic_id): return None

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    app.dependency_overrides[dental_ai.require_doctor] = doctor
    response = TestClient(app).post(
        "/v1/dental/ai/doctor/patient-summary",
        json={"patient_id": "patient-b"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PATIENT_CLINIC_ACCESS_DENIED"
    app.dependency_overrides.clear()


def test_scan_adapter_status_is_reported(monkeypatch):
    class Store:
        def ai_usage_count(self, *args, **kwargs): return 0
        def get_patient(self, patient_id, clinic_id): return {"id": patient_id, "clinic_id": clinic_id, "age": "30"}
        def chart(self, *args): return []
        def periodontogram(self, *args): return []
        def appointments(self, *args): return []
        def record_ai_usage(self, *args): pass

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    monkeypatch.setenv("SCANO_MODE", "disabled")
    app.dependency_overrides[dental_ai.require_doctor] = doctor
    response = TestClient(app).post(
        "/v1/dental/ai/doctor/scan-analysis",
        json={"patient_id": "p1", "scan_reference": "scan-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["adapter"]["provider"] == "scanO"
    assert body["adapter"]["configured"] is False
    assert body["result"]["status"] == "adapter_unavailable"
    app.dependency_overrides.clear()


def test_doctor_approval_is_audit_only(monkeypatch):
    class Store:
        def get_patient(self, patient_id, clinic_id): return {"id": patient_id, "clinic_id": clinic_id}
        def chart(self, *args): return []
        def periodontogram(self, *args): return []
        def appointments(self, *args): return []
        def save_ai_approval(self, *args): return "approval-1"

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    app.dependency_overrides[dental_ai.require_doctor] = doctor
    response = TestClient(app).post(
        "/v1/dental/ai/doctor/approve",
        json={"patient_id": "p1", "capability": "treatment_plan", "action": "accepted", "content": {"result": "suggestion"}},
    )
    assert response.status_code == 200
    assert response.json()["result"]["written_to_clinical_record"] is False
    assert response.json()["result"]["approval_id"] == "approval-1"
    app.dependency_overrides.clear()
