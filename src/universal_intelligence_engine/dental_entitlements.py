"""Server-authoritative Oralyn clinic trial and entitlement lifecycle."""

from __future__ import annotations

import os
import time
import uuid

from fastapi import HTTPException

from .dental_store import get_dental_store

TRIAL_SECONDS = 72 * 60 * 60


def _is_postgres(store) -> bool:
    return bool(store.database_url)


def get_clinic_entitlement(clinic_id: str, *, connection=None) -> dict | None:
    store = get_dental_store()
    sql = (
        "SELECT * FROM dental_clinic_entitlements WHERE clinic_id=%s"
        if _is_postgres(store)
        else "SELECT * FROM dental_clinic_entitlements WHERE clinic_id=?"
    )
    rows = store._execute(sql, (clinic_id,), connection=connection)
    if not rows:
        return None
    item = rows[0]
    if (
        item["entitlement_type"] == "trial"
        and item["status"] == "trialing"
        and item.get("trial_ends_at") is not None
        and int(item["trial_ends_at"]) <= int(time.time())
    ):
        update_sql = (
            "UPDATE dental_clinic_entitlements SET status=%s,updated_at=%s WHERE clinic_id=%s AND status='trialing'"
            if _is_postgres(store)
            else "UPDATE dental_clinic_entitlements SET status=?,updated_at=? WHERE clinic_id=? AND status='trialing'"
        )
        store._execute(update_sql, ("expired", str(int(time.time())), clinic_id), connection=connection)
        item["status"] = "expired"
    return item


def ensure_clinic_entitlement(clinic_id: str, *, connection=None) -> dict:
    """Create one lifetime-free or 72-hour entitlement exactly once per clinic."""
    store = get_dental_store()
    current = get_clinic_entitlement(clinic_id, connection=connection)
    master_clinic_id = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()

    if master_clinic_id and clinic_id == master_clinic_id:
        now = str(int(time.time()))
        if current:
            if current["entitlement_type"] != "founding_free":
                update_sql = (
                    "UPDATE dental_clinic_entitlements SET entitlement_type=%s,status=%s,plan_id=%s,trial_started_at=NULL,trial_ends_at=NULL,current_period_end=NULL,source_order_id=NULL,updated_at=%s WHERE clinic_id=%s"
                    if _is_postgres(store)
                    else "UPDATE dental_clinic_entitlements SET entitlement_type=?,status=?,plan_id=?,trial_started_at=NULL,trial_ends_at=NULL,current_period_end=NULL,source_order_id=NULL,updated_at=? WHERE clinic_id=?"
                )
                store._execute(update_sql, ("founding_free", "lifetime_free", "founding", now, clinic_id), connection=connection)
        else:
            insert_sql = (
                "INSERT INTO dental_clinic_entitlements(id,clinic_id,entitlement_type,status,plan_id,trial_started_at,trial_ends_at,current_period_end,source_order_id,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,%s,%s) ON CONFLICT (clinic_id) DO NOTHING"
                if _is_postgres(store)
                else "INSERT INTO dental_clinic_entitlements(id,clinic_id,entitlement_type,status,plan_id,trial_started_at,trial_ends_at,current_period_end,source_order_id,created_at,updated_at) VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,?,?) ON CONFLICT(clinic_id) DO NOTHING"
            )
            store._execute(insert_sql, (str(uuid.uuid4()), clinic_id, "founding_free", "lifetime_free", "founding", now, now), connection=connection)
        return get_clinic_entitlement(clinic_id, connection=connection) or {}

    if current:
        return current

    now = int(time.time())
    started = now
    ends = started + TRIAL_SECONDS
    created_at = str(now)
    insert_sql = (
        "INSERT INTO dental_clinic_entitlements(id,clinic_id,entitlement_type,status,plan_id,trial_started_at,trial_ends_at,current_period_end,source_order_id,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s) ON CONFLICT (clinic_id) DO NOTHING"
        if _is_postgres(store)
        else "INSERT INTO dental_clinic_entitlements(id,clinic_id,entitlement_type,status,plan_id,trial_started_at,trial_ends_at,current_period_end,source_order_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?) ON CONFLICT(clinic_id) DO NOTHING"
    )
    store._execute(
        insert_sql,
        (str(uuid.uuid4()), clinic_id, "trial", "trialing", "clinic", started, ends, created_at, created_at),
        connection=connection,
    )
    result = get_clinic_entitlement(clinic_id, connection=connection)
    if not result:
        raise HTTPException(503, "Clinic entitlement could not be initialized")
    return result


def activate_paid_entitlement(
    clinic_id: str, plan_id: str, order_id: str, *, period_end: int | None = None
) -> dict:
    store = get_dental_store()
    if plan_id not in {"clinic", "enterprise"}:
        raise HTTPException(400, "Unsupported subscription plan")
    current = ensure_clinic_entitlement(clinic_id)
    if current.get("entitlement_type") == "founding_free":
        return current
    now = int(time.time())
    ends = period_end or (now + 30 * 86400)
    sql = (
        "UPDATE dental_clinic_entitlements SET entitlement_type=%s,status=%s,plan_id=%s,current_period_end=%s,source_order_id=%s,updated_at=%s WHERE clinic_id=%s"
        if _is_postgres(store)
        else "UPDATE dental_clinic_entitlements SET entitlement_type=?,status=?,plan_id=?,current_period_end=?,source_order_id=?,updated_at=? WHERE clinic_id=?"
    )
    store._execute(sql, ("subscription", "active", plan_id, ends, order_id, str(now), clinic_id))
    return get_clinic_entitlement(clinic_id) or {}


def require_entitled(clinic_id: str) -> dict:
    entitlement = get_clinic_entitlement(clinic_id)
    if not entitlement:
        entitlement = ensure_clinic_entitlement(clinic_id)
    if entitlement["status"] not in {"trialing", "active", "lifetime_free"}:
        raise HTTPException(
            402,
            detail={
                "code": "CLINIC_SUBSCRIPTION_REQUIRED",
                "message": "The clinic demo has ended. Choose a subscription to restore full access.",
                "entitlement": entitlement,
            },
        )
    return entitlement
