"""Tenant-scoped inventory catalog and stock movement ledger."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .dental_auth import require_doctor
from .dental_store import get_dental_store

router = APIRouter(prefix="/v1/dental", tags=["dental-inventory"])


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _query(store, pg: str, sqlite: str, params: tuple = (), connection=None):
    return store._execute(pg if store.database_url else sqlite, params, connection=connection)


def _clinic(actor: dict) -> str:
    clinic_id = str(actor.get("clinic_id") or "").strip()
    if not clinic_id:
        raise HTTPException(403, detail={"code": "CLINIC_CONTEXT_REQUIRED", "message": "Clinic context is required."})
    return clinic_id


class SupplierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=320)
    gstin: str = Field(default="", max_length=20)
    address: str = Field(default="", max_length=2000)


class InventoryItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(default="", max_length=80)
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(default="general", min_length=2, max_length=80)
    unit: str = Field(default="unit", min_length=1, max_length=30)
    reorder_level: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=3)
    default_unit_cost: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=2)
    supplier_id: str | None = Field(default=None, max_length=64)


class StockMovementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement_type: Literal["receipt", "consumption", "adjustment", "return"]
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_cost: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=2)
    supplier_id: str | None = Field(default=None, max_length=64)
    batch_number: str = Field(default="", max_length=100)
    expires_on: date | None = None
    reference_type: str = Field(default="", max_length=40)
    reference_id: str = Field(default="", max_length=128)
    reason: str = Field(default="", max_length=500)


@router.get("/inventory/suppliers")
def list_suppliers(actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    return {"suppliers": _query(
        store,
        "SELECT id,name,contact_name,phone,email,gstin,address,active,created_at FROM dental_inventory_suppliers WHERE clinic_id=%s ORDER BY name",
        "SELECT id,name,contact_name,phone,email,gstin,address,active,created_at FROM dental_inventory_suppliers WHERE clinic_id=? ORDER BY name",
        (_clinic(actor),),
    )}


@router.post("/inventory/suppliers", status_code=201)
def create_supplier(body: SupplierIn, actor: Annotated[dict, Depends(require_doctor)]):
    if actor.get("role") not in {"owner", "admin", "doctor", "dentist"}:
        raise HTTPException(403, detail={"code": "INVENTORY_ACCESS_DENIED", "message": "Clinic inventory access is required."})
    store = get_dental_store()
    clinic_id = _clinic(actor)
    item = {"id": _id(), "clinic_id": clinic_id, **body.model_dump(), "name": body.name.strip(), "created_at": _now()}
    try:
        with store.transaction() as connection:
            _query(store,
                "INSERT INTO dental_inventory_suppliers(id,clinic_id,name,contact_name,phone,email,gstin,address,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s)",
                "INSERT INTO dental_inventory_suppliers(id,clinic_id,name,contact_name,phone,email,gstin,address,active,created_at) VALUES (?,?,?,?,?,?,?,?,1,?)",
                tuple(item[k] for k in ("id","clinic_id","name","contact_name","phone","email","gstin","address","created_at")), connection)
            store.audit(actor["id"], "inventory.supplier_created", "inventory_supplier", item["id"], clinic_id, connection)
    except Exception as exc:
        raise HTTPException(409, detail={"code": "SUPPLIER_ALREADY_EXISTS", "message": "A supplier with this name already exists in this clinic."}) from exc
    item.pop("clinic_id")
    return item


@router.get("/inventory/items")
def list_items(actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    return {"items": _query(store,
        "SELECT id,sku,name,category,unit,reorder_level,quantity_on_hand,default_unit_cost,supplier_id,active,created_at,updated_at FROM dental_inventory_items WHERE clinic_id=%s AND active=true ORDER BY name",
        "SELECT id,sku,name,category,unit,reorder_level,quantity_on_hand,default_unit_cost,supplier_id,active,created_at,updated_at FROM dental_inventory_items WHERE clinic_id=? AND active=1 ORDER BY name",
        (_clinic(actor),))}


@router.post("/inventory/items", status_code=201)
def create_item(body: InventoryItemIn, actor: Annotated[dict, Depends(require_doctor)]):
    if actor.get("role") not in {"owner", "admin", "doctor", "dentist"}:
        raise HTTPException(403, detail={"code": "INVENTORY_ACCESS_DENIED", "message": "Clinic inventory access is required."})
    store = get_dental_store()
    clinic_id = _clinic(actor)
    sku = body.sku.strip()
    if body.supplier_id:
        supplier = _query(store,
            "SELECT id FROM dental_inventory_suppliers WHERE id=%s AND clinic_id=%s AND active=true",
            "SELECT id FROM dental_inventory_suppliers WHERE id=? AND clinic_id=? AND active=1",
            (body.supplier_id, clinic_id))
        if not supplier:
            raise HTTPException(404, detail={"code": "SUPPLIER_NOT_FOUND", "message": "Supplier not found in this clinic."})
    if sku and _query(store,
        "SELECT id FROM dental_inventory_items WHERE clinic_id=%s AND sku=%s",
        "SELECT id FROM dental_inventory_items WHERE clinic_id=? AND sku=?",
        (clinic_id, sku)):
        raise HTTPException(409, detail={"code": "INVENTORY_SKU_EXISTS", "message": "This SKU already exists in this clinic."})
    now = _now()
    item = {"id": _id(), "clinic_id": clinic_id, "supplier_id": body.supplier_id, "sku": sku, "name": body.name.strip(), "category": body.category.strip(), "unit": body.unit.strip(), "reorder_level": str(body.reorder_level), "default_unit_cost": str(body.default_unit_cost), "created_at": now, "updated_at": now}
    with store.transaction() as connection:
        _query(store,
            "INSERT INTO dental_inventory_items(id,clinic_id,supplier_id,sku,name,category,unit,reorder_level,quantity_on_hand,default_unit_cost,active,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,true,%s,%s)",
            "INSERT INTO dental_inventory_items(id,clinic_id,supplier_id,sku,name,category,unit,reorder_level,quantity_on_hand,default_unit_cost,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,1,?,?)",
            tuple(item[k] for k in ("id","clinic_id","supplier_id","sku","name","category","unit","reorder_level","default_unit_cost","created_at","updated_at")), connection)
        store.audit(actor["id"], "inventory.item_created", "inventory_item", item["id"], clinic_id, connection)
    return {**item, "quantity_on_hand": "0", "active": True}


@router.post("/inventory/items/{item_id}/movements", status_code=201)
def create_movement(item_id: str, body: StockMovementIn, actor: Annotated[dict, Depends(require_doctor)]):
    if actor.get("role") not in {"owner", "admin", "doctor", "dentist"}:
        raise HTTPException(403, detail={"code": "INVENTORY_ACCESS_DENIED", "message": "Clinic inventory access is required."})
    store = get_dental_store()
    clinic_id = _clinic(actor)
    if body.movement_type == "adjustment":
        raise HTTPException(422, detail={"code": "STOCKTAKE_ADJUSTMENT_NOT_ENABLED", "message": "Use receipt or consumption until the dual-approval stocktake workflow is enabled."})
    delta = body.quantity if body.movement_type in {"receipt", "return"} else -body.quantity
    if body.movement_type == "receipt" and not body.supplier_id:
        raise HTTPException(422, detail={"code": "SUPPLIER_REQUIRED", "message": "A supplier is required for stock receipts."})
    if body.movement_type == "consumption" and not body.reason.strip():
        raise HTTPException(422, detail={"code": "CONSUMPTION_REASON_REQUIRED", "message": "Record the reason for stock consumption."})
    now = _now()
    movement_id = _id()
    with store.transaction() as connection:
        lock = " FOR UPDATE" if store.database_url else ""
        rows = _query(store,
            "SELECT id,quantity_on_hand FROM dental_inventory_items WHERE id=%s AND clinic_id=%s AND active=true" + lock,
            "SELECT id,quantity_on_hand FROM dental_inventory_items WHERE id=? AND clinic_id=? AND active=1",
            (item_id, clinic_id), connection)
        if not rows:
            raise HTTPException(404, detail={"code": "INVENTORY_ITEM_NOT_FOUND", "message": "Inventory item not found in this clinic."})
        if body.supplier_id:
            supplier = _query(store,
                "SELECT id FROM dental_inventory_suppliers WHERE id=%s AND clinic_id=%s AND active=true",
                "SELECT id FROM dental_inventory_suppliers WHERE id=? AND clinic_id=? AND active=1",
                (body.supplier_id, clinic_id), connection)
            if not supplier:
                raise HTTPException(404, detail={"code": "SUPPLIER_NOT_FOUND", "message": "Supplier not found in this clinic."})
        updated = Decimal(str(rows[0]["quantity_on_hand"])) + delta
        if updated < 0:
            raise HTTPException(409, detail={"code": "INSUFFICIENT_STOCK", "message": "This movement would make stock negative."})
        _query(store,
            "UPDATE dental_inventory_items SET quantity_on_hand=%s,updated_at=%s WHERE id=%s AND clinic_id=%s",
            "UPDATE dental_inventory_items SET quantity_on_hand=?,updated_at=? WHERE id=? AND clinic_id=?",
            (str(updated), now, item_id, clinic_id), connection)
        _query(store,
            "INSERT INTO dental_inventory_movements(id,clinic_id,item_id,supplier_id,movement_type,quantity_delta,unit_cost,batch_number,expires_on,reference_type,reference_id,reason,actor_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            "INSERT INTO dental_inventory_movements(id,clinic_id,item_id,supplier_id,movement_type,quantity_delta,unit_cost,batch_number,expires_on,reference_type,reference_id,reason,actor_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (movement_id, clinic_id, item_id, body.supplier_id, body.movement_type, str(delta), str(body.unit_cost), body.batch_number.strip(), body.expires_on.isoformat() if body.expires_on else None, body.reference_type.strip(), body.reference_id.strip(), body.reason.strip(), actor["id"], now), connection)
        store.audit(actor["id"], "inventory.stock_moved", "inventory_item", item_id, clinic_id, connection)
    return {"id": movement_id, "item_id": item_id, "movement_type": body.movement_type, "quantity_delta": str(delta), "quantity_on_hand": str(updated), "created_at": now}


@router.get("/inventory/items/{item_id}/movements")
def list_movements(item_id: str, actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    clinic_id = _clinic(actor)
    exists = _query(store,
        "SELECT id FROM dental_inventory_items WHERE id=%s AND clinic_id=%s",
        "SELECT id FROM dental_inventory_items WHERE id=? AND clinic_id=?",
        (item_id, clinic_id))
    if not exists:
        raise HTTPException(404, detail={"code": "INVENTORY_ITEM_NOT_FOUND", "message": "Inventory item not found in this clinic."})
    rows = _query(store,
        "SELECT id,movement_type,quantity_delta,unit_cost,batch_number,expires_on,supplier_id,reference_type,reference_id,reason,actor_id,created_at FROM dental_inventory_movements WHERE item_id=%s AND clinic_id=%s ORDER BY created_at DESC,id DESC",
        "SELECT id,movement_type,quantity_delta,unit_cost,batch_number,expires_on,supplier_id,reference_type,reference_id,reason,actor_id,created_at FROM dental_inventory_movements WHERE item_id=? AND clinic_id=? ORDER BY created_at DESC,id DESC",
        (item_id, clinic_id))
    return {"movements": rows}


@router.get("/inventory/alerts")
def low_stock_alerts(actor: Annotated[dict, Depends(require_doctor)]):
    store = get_dental_store()
    rows = _query(store,
        "SELECT id,sku,name,unit,quantity_on_hand,reorder_level FROM dental_inventory_items WHERE clinic_id=%s AND active=true AND quantity_on_hand<=reorder_level ORDER BY name",
        "SELECT id,sku,name,unit,quantity_on_hand,reorder_level FROM dental_inventory_items WHERE clinic_id=? AND active=1 AND quantity_on_hand<=reorder_level ORDER BY name",
        (_clinic(actor),))
    return {"low_stock": rows, "count": len(rows)}
