from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine.dental_ai import router, _triage_emergency
from universal_intelligence_engine import dental_ai

app = FastAPI()
app.include_router(router)


def doctor():
    return {"id": "doctor-1", "clinic_id": "clinic-a", "role": "doctor"}


def patient():
    return {"user_id": "patient-user", "email": "patient@example.com"}


def test_triage_emergency_path_flags_breathing_difficulty():
    flagged, reasons = _triage_emergency("My face is swelling and I have difficulty breathing.")
    assert flagged is True
    assert "difficulty breathing" in reasons
    assert "rapid facial/neck swelling" in reasons


def test_patient_chat_emergency_does_not_call_ai(monkeypatch):
    called = {"value": False}

    async def fail(*args, **kwargs):
        called["value"] = True
        raise AssertionError("AI must not be called for deterministic emergency flag")

    monkeypatch.setattr(dental_ai, "_patient_ai", fail)
    app.dependency_overrides[dental_ai.require_patient] = patient
    response = TestClient(app).post(
        "/v1/dental/ai/patient/chat",
        json={"message": "I cannot breathe and my neck is swelling rapidly"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["emergency"] is True
    assert body["request_id"]
    assert called["value"] is False
    app.dependency_overrides.clear()


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


def test_patient_ai_refuses_without_consent(monkeypatch):
    class Store:
        def ai_consent(self, user_id): return False

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    app.dependency_overrides[dental_ai.require_patient] = patient
    response = TestClient(app).post(
        "/v1/dental/ai/patient/aftercare",
        json={"treatment": "root canal"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AI_CONSENT_REQUIRED"
    app.dependency_overrides.clear()


def test_patient_ai_prompt_forbids_diagnosis_and_dosage(monkeypatch):
    captured = {}

    class Store:
        def ai_consent(self, user_id): return True
        def patient_by_user_id(self, user_id, patient_id=None): return None
        def ai_usage_count(self, *args, **kwargs): return 0
        def save_patient_ai_message(self, *args): pass
        def record_ai_usage(self, *args): pass

    async def fake_uie(capability, goal, context, disclaimer):
        captured["goal"] = goal
        return {"result": "safe", "confidence": 0.8, "sources": [], "disclaimers": [disclaimer], "request_id": "r"}

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    monkeypatch.setattr(dental_ai, "_uie", fake_uie)
    app.dependency_overrides[dental_ai.require_patient] = patient
    response = TestClient(app).post(
        "/v1/dental/ai/patient/chat",
        json={"message": "What should I do about tooth pain?"},
    )
    assert response.status_code == 200
    assert "Never diagnose" in captured["goal"]
    assert "Never prescribe" in captured["goal"]
    assert "dosage" in captured["goal"]
    assert "not a diagnosis" in response.json()["disclaimers"][0].lower()
    app.dependency_overrides.clear()


def test_ai_user_limit_blocks_before_provider(monkeypatch):
    class Store:
        def ai_consent(self, user_id): return True
        def patient_by_user_id(self, user_id, patient_id=None): return None
        def ai_usage_count(self, actor_id, since_epoch, clinic_id=None): return 50 if clinic_id is None else 0

    called = {"value": False}
    async def fail(*args, **kwargs):
        called["value"] = True
        raise AssertionError("provider must not be called after limit")

    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    monkeypatch.setattr(dental_ai, "_uie", fail)
    app.dependency_overrides[dental_ai.require_patient] = patient
    response = TestClient(app).post("/v1/dental/ai/patient/chat", json={"message": "hello"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "AI_USER_RATE_LIMIT"
    assert called["value"] is False
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


def test_patient_context_defaults_to_anonymized(monkeypatch):
    class Store:
        def get_patient(self, patient_id, clinic_id): return {"id": patient_id, "clinic_id": clinic_id, "age": 41}
        def chart(self, *args): return [{"tooth_fdi": "16", "status": "caries"}]
        def periodontogram(self, *args): return [{"tooth_fdi": "16", "measurements": {"pd": 4}}]
        def appointments(self, *args): return [{"starts_at": "2026-10-08T10:00:00", "treatment_type": "RCT", "status": "scheduled"}]
    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    monkeypatch.delenv("AI_PATIENT_DATA_MODE", raising=False)
    context = dental_ai._safe_context("patient-123", "clinic-a")
    assert "patient_id" not in context
    assert context["patient"] == {"age_band": "30_44"}
    assert context["appointments"] == [{"treatment_type": "RCT", "status": "scheduled"}]
    assert "starts_at" not in context["appointments"][0]


def test_patient_context_full_mode_is_explicit_opt_in(monkeypatch):
    class Store:
        def get_patient(self, patient_id, clinic_id): return {"id": patient_id, "clinic_id": clinic_id, "age": 41}
        def chart(self, *args): return []
        def periodontogram(self, *args): return []
        def appointments(self, *args): return [{"starts_at": "2026-10-08T10:00:00", "treatment_type": "RCT", "status": "scheduled"}]
    monkeypatch.setattr(dental_ai, "get_dental_store", lambda: Store())
    monkeypatch.setenv("AI_PATIENT_DATA_MODE", "full")
    context = dental_ai._safe_context("patient-123", "clinic-a")
    assert context["patient"]["patient_id"] == "patient-123"
    assert context["patient"]["age"] == 41
    assert context["appointments"][0]["starts_at"] == "2026-10-08T10:00:00"
