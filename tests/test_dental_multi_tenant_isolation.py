from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_api, dental_auth
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def isolated_clinics(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "clinic-isolation.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(database_path))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv(
        "ALEMBIC_CONFIG",
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
    )
    store = DentalStore()
    for organisation in (
        {"id": "org-a", "name": "Clinic A Group", "organisation_type": "group"},
        {"id": "org-b", "name": "Clinic B Group", "organisation_type": "group"},
    ):
        store.create_organisation(organisation)
    for clinic in (
        {
            "id": "clinic-a",
            "organisation_id": "org-a",
            "name": "Clinic A",
            "clinic_type": "clinic",
        },
        {
            "id": "clinic-b",
            "organisation_id": "org-b",
            "name": "Clinic B",
            "clinic_type": "clinic",
        },
    ):
        store.create_clinic(clinic)

    for doctor_id, email, clinic_id in (
        ("doctor-a", "doctor-a@example.test", "clinic-a"),
        ("doctor-b", "doctor-b@example.test", "clinic-b"),
    ):
        store.create_doctor(
            {
                "id": doctor_id,
                "email": email,
                "password_hash": dental_auth.hash_password("correct horse battery staple"),
                "role": "doctor",
                "clinic_id": clinic_id,
            }
        )
        store.create_doctor_membership(doctor_id, clinic_id, "owner")

    patient_a = store.create_patient(
        {"name": "Patient A", "phone": "9000000001", "age": "35"}, "clinic-a"
    )
    patient_b = store.create_patient(
        {"name": "Patient B", "phone": "9000000002", "age": "42"}, "clinic-b"
    )
    appointment_a = store.create_appointment(
        {
            "patient_id": patient_a["id"],
            "starts_at": "2026-11-10T10:00:00",
            "treatment_type": "Checkup",
            "status": "scheduled",
        },
        "clinic-a",
    )
    appointment_b = store.create_appointment(
        {
            "patient_id": patient_b["id"],
            "starts_at": "2026-11-10T11:00:00",
            "treatment_type": "Root canal",
            "status": "scheduled",
        },
        "clinic-b",
    )
    store.save_chart(
        {
            "patient_id": patient_a["id"],
            "tooth_fdi": "11",
            "status": "healthy",
            "note": "",
        },
        "clinic-a",
    )
    store.save_chart(
        {
            "patient_id": patient_b["id"],
            "tooth_fdi": "21",
            "status": "treated",
            "note": "",
        },
        "clinic-b",
    )
    store.save_periodontogram(
        {
            "patient_id": patient_a["id"],
            "tooth_fdi": "11",
            "measurements": {"B": "2"},
            "note": "",
        },
        "clinic-a",
    )
    store.save_periodontogram(
        {
            "patient_id": patient_b["id"],
            "tooth_fdi": "21",
            "measurements": {"B": "5"},
            "note": "",
        },
        "clinic-b",
    )

    monkeypatch.setattr(dental_auth, "get_dental_store", lambda: store)
    monkeypatch.setattr(dental_api, "get_dental_store", lambda: store)
    dental_auth._LOGIN_ATTEMPTS.clear()
    app = FastAPI()
    app.include_router(dental_api.router)
    client = TestClient(app)

    def login(email: str) -> dict[str, str]:
        response = client.post(
            "/v1/dental/auth/password/login",
            json={
                "email": email,
                "password": "correct horse battery staple",
            },
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return {
        "client": client,
        "store": store,
        "login": login,
        "patient_a": patient_a,
        "patient_b": patient_b,
        "appointment_a": appointment_a,
        "appointment_b": appointment_b,
    }


def test_patient_and_appointment_lists_are_isolated_by_active_workspace(isolated_clinics):
    client = isolated_clinics["client"]
    headers_a = isolated_clinics["login"]("doctor-a@example.test")
    headers_b = isolated_clinics["login"]("doctor-b@example.test")

    patients_a = client.get("/v1/dental/patients", headers=headers_a).json()
    patients_b = client.get("/v1/dental/patients", headers=headers_b).json()
    appointments_a = client.get("/v1/dental/appointments", headers=headers_a).json()
    appointments_b = client.get("/v1/dental/appointments", headers=headers_b).json()

    assert [row["id"] for row in patients_a] == [isolated_clinics["patient_a"]["id"]]
    assert [row["id"] for row in patients_b] == [isolated_clinics["patient_b"]["id"]]
    assert [row["id"] for row in appointments_a] == [isolated_clinics["appointment_a"]["id"]]
    assert [row["id"] for row in appointments_b] == [isolated_clinics["appointment_b"]["id"]]


def test_foreign_clinic_chart_and_periodontal_reads_return_no_records(isolated_clinics):
    client = isolated_clinics["client"]
    headers_a = isolated_clinics["login"]("doctor-a@example.test")
    patient_b_id = isolated_clinics["patient_b"]["id"]

    chart = client.get(
        f"/v1/dental/patients/{patient_b_id}/chart", headers=headers_a
    )
    perio = client.get(
        f"/v1/dental/patients/{patient_b_id}/periodontogram", headers=headers_a
    )

    assert chart.status_code == 200
    assert chart.json() == []
    assert perio.status_code == 200
    assert perio.json() == []


def test_foreign_clinic_patient_cannot_be_used_for_writes_or_updates(isolated_clinics):
    client = isolated_clinics["client"]
    headers_a = isolated_clinics["login"]("doctor-a@example.test")
    patient_b_id = isolated_clinics["patient_b"]["id"]
    appointment_b_id = isolated_clinics["appointment_b"]["id"]

    update = client.patch(
        f"/v1/dental/appointments/{appointment_b_id}",
        headers=headers_a,
        json={"status": "completed"},
    )
    create_appointment = client.post(
        "/v1/dental/appointments",
        headers=headers_a,
        json={
            "patient_id": patient_b_id,
            "starts_at": "2026-11-11T10:00:00",
            "treatment_type": "Cross-clinic attempt",
        },
    )
    save_chart = client.post(
        f"/v1/dental/patients/{patient_b_id}/chart",
        headers=headers_a,
        json={"patient_id": patient_b_id, "tooth_fdi": "22", "status": "healthy"},
    )
    save_perio = client.post(
        f"/v1/dental/patients/{patient_b_id}/periodontogram",
        headers=headers_a,
        json={
            "patient_id": patient_b_id,
            "tooth_fdi": "22",
            "measurements": {"B": "3"},
        },
    )
    scan = client.post(
        "/v1/dental/scans/screen",
        headers=headers_a,
        json={"patient_id": patient_b_id, "scan_reference": "private-scan-reference"},
    )
    intelligence = client.post(
        "/v1/dental/intelligence/run",
        headers=headers_a,
        json={"patient_id": patient_b_id, "goal": "Summarise this patient"},
    )

    assert update.status_code == 404
    assert create_appointment.status_code == 404
    assert save_chart.status_code == 404
    assert save_perio.status_code == 404
    assert scan.status_code == 403
    assert intelligence.status_code == 403


def test_clinic_audit_events_include_the_active_workspace(isolated_clinics):
    client = isolated_clinics["client"]
    store = isolated_clinics["store"]
    headers_a = isolated_clinics["login"]("doctor-a@example.test")
    patient_a_id = isolated_clinics["patient_a"]["id"]
    appointment_a_id = isolated_clinics["appointment_a"]["id"]

    created_patient = client.post(
        "/v1/dental/patients",
        headers=headers_a,
        json={"name": "New Clinic A Patient", "phone": "9000000099", "age": "29"},
    )
    assert created_patient.status_code == 200
    created_appointment = client.post(
        "/v1/dental/appointments",
        headers=headers_a,
        json={
            "patient_id": patient_a_id,
            "starts_at": "2026-11-12T10:00:00",
            "treatment_type": "Cleaning",
        },
    )
    assert created_appointment.status_code == 200
    updated_appointment = client.patch(
        f"/v1/dental/appointments/{appointment_a_id}",
        headers=headers_a,
        json={"status": "completed"},
    )
    assert updated_appointment.status_code == 200
    chart = client.post(
        f"/v1/dental/patients/{patient_a_id}/chart",
        headers=headers_a,
        json={"patient_id": patient_a_id, "tooth_fdi": "12", "status": "healthy"},
    )
    assert chart.status_code == 200
    perio = client.post(
        f"/v1/dental/patients/{patient_a_id}/periodontogram",
        headers=headers_a,
        json={
            "patient_id": patient_a_id,
            "tooth_fdi": "12",
            "measurements": {"B": "2"},
        },
    )
    assert perio.status_code == 200

    events = store._execute(
        "SELECT action, clinic_id FROM dental_audit_events "
        "WHERE actor_id=? ORDER BY created_at",
        ("doctor-a",),
    )
    assert len(events) >= 5
    assert {event["clinic_id"] for event in events} == {"clinic-a"}
