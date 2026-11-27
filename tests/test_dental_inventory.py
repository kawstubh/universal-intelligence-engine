from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_intelligence_engine import dental_inventory
from universal_intelligence_engine.dental_store import DentalStore


@pytest.fixture
def inventory_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "inventory.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("ALEMBIC_CONFIG", str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    store = DentalStore()
    store.create_organisation({"id": "org-a", "name": "Clinic A", "organisation_type": "clinic"})
    store.create_organisation({"id": "org-b", "name": "Clinic B", "organisation_type": "clinic"})
    store.create_clinic({"id": "clinic-a", "organisation_id": "org-a", "name": "Clinic A", "clinic_type": "clinic"})
    store.create_clinic({"id": "clinic-b", "organisation_id": "org-b", "name": "Clinic B", "clinic_type": "clinic"})
    actor = {"id": "doctor-a", "clinic_id": "clinic-a", "role": "owner"}
    monkeypatch.setattr(dental_inventory, "get_dental_store", lambda: store)
    app = FastAPI()
    app.include_router(dental_inventory.router)
    app.dependency_overrides[dental_inventory.require_doctor] = lambda: actor
    return TestClient(app), store, actor


def test_inventory_supplier_item_receipt_consumption_and_alerts(inventory_api):
    client, store, _actor = inventory_api
    supplier = client.post("/v1/dental/inventory/suppliers", json={"name": "Dental Supply Co"})
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["id"]

    item = client.post("/v1/dental/inventory/items", json={
        "sku": "GLOVE-M", "name": "Examination Gloves", "category": "consumables",
        "unit": "box", "reorder_level": "2", "default_unit_cost": "125.00",
        "supplier_id": supplier_id,
    })
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    assert item.json()["quantity_on_hand"] == "0"

    receipt = client.post(f"/v1/dental/inventory/items/{item_id}/movements", json={
        "movement_type": "receipt", "quantity": "10", "unit_cost": "125",
        "supplier_id": supplier_id, "batch_number": "BATCH-01", "expires_on": "2027-12-31",
        "reference_type": "purchase_receipt", "reference_id": "GRN-001",
    })
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["quantity_on_hand"] == "10"

    consumption = client.post(f"/v1/dental/inventory/items/{item_id}/movements", json={
        "movement_type": "consumption", "quantity": "9", "reason": "Clinical use",
        "reference_type": "treatment", "reference_id": "TX-001",
    })
    assert consumption.status_code == 201, consumption.text
    assert consumption.json()["quantity_on_hand"] == "1"

    alerts = client.get("/v1/dental/inventory/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["count"] == 1
    assert alerts.json()["low_stock"][0]["id"] == item_id

    history = client.get(f"/v1/dental/inventory/items/{item_id}/movements")
    assert history.status_code == 200
    assert len(history.json()["movements"]) == 2
    assert store._execute("SELECT COUNT(*) AS n FROM dental_audit_events WHERE action LIKE 'inventory.%'")[0]["n"] == 4


def test_inventory_prevents_negative_stock_and_unsafe_adjustment(inventory_api):
    client, _store, _actor = inventory_api
    item = client.post("/v1/dental/inventory/items", json={"name": "Composite Syringe"})
    assert item.status_code == 201
    item_id = item.json()["id"]
    response = client.post(f"/v1/dental/inventory/items/{item_id}/movements", json={
        "movement_type": "consumption", "quantity": "1", "reason": "Clinical use",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INSUFFICIENT_STOCK"
    adjustment = client.post(f"/v1/dental/inventory/items/{item_id}/movements", json={
        "movement_type": "adjustment", "quantity": "1", "reason": "Stocktake",
    })
    assert adjustment.status_code == 422
    assert adjustment.json()["detail"]["code"] == "STOCKTAKE_ADJUSTMENT_NOT_ENABLED"


def test_inventory_is_tenant_scoped_and_rejects_foreign_supplier(inventory_api):
    client, _store, actor = inventory_api
    supplier = client.post("/v1/dental/inventory/suppliers", json={"name": "Clinic A Supplier"})
    assert supplier.status_code == 201
    actor["clinic_id"] = "clinic-b"
    assert client.get("/v1/dental/inventory/suppliers").json()["suppliers"] == []
    foreign = client.post("/v1/dental/inventory/items", json={
        "name": "Material", "supplier_id": supplier.json()["id"],
    })
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "SUPPLIER_NOT_FOUND"
    assert client.get(f"/v1/dental/inventory/items/{supplier.json()['id']}/movements").status_code == 404


def test_inventory_routes_require_doctor_authentication():
    app = FastAPI()
    app.include_router(dental_inventory.router)
    client = TestClient(app)
    assert client.get("/v1/dental/inventory/items").status_code == 401
    assert client.post("/v1/dental/inventory/items", json={"name": "Material"}).status_code == 401
