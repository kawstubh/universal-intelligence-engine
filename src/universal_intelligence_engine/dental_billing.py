"""Dental SaaS membership catalog and verified Razorpay lifecycle handling."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
from fastapi import HTTPException

from .dental_store import get_dental_store

PLANS = [
    {"id": "clinic", "name": "Clinic", "price_inr": 4999, "period": "month", "tagline": "Full dental platform for growing clinics",
     "features": ["Up to 10 doctors", "Appointments & patient records", "Dental chart + periodontogram", "Treatment planning", "AI clinical workspace", "Staff roles", "Scan/WhatsApp integrations", "Priority support"]},
    {"id": "enterprise", "name": "Enterprise", "price_inr": 9999, "period": "month", "tagline": "For groups, chains and multi-location operations",
     "features": ["Unlimited clinic staff", "Multi-location support", "Advanced AI", "Custom integrations", "Dedicated onboarding", "Advanced limits and SLA"]},
]


def plans() -> list[dict]:
    return PLANS


def _plan(plan_id: str) -> dict:
    for p in PLANS:
        if p["id"] == plan_id:
            return p
    raise HTTPException(404, "Membership plan not found")


def _period_end(base: str | None = None) -> str:
    now = int(time.time())
    try:
        existing = int(float(base or "0"))
    except (TypeError, ValueError):
        existing = 0
    return str(max(now, existing) + 30 * 86400)


def _membership(clinic_id: str):
    store = get_dental_store()
    rows = store._execute(
        "SELECT * FROM dental_memberships WHERE clinic_id=%s ORDER BY created_at DESC LIMIT 1" if store.database_url else
        "SELECT * FROM dental_memberships WHERE clinic_id=? ORDER BY created_at DESC LIMIT 1",
        (clinic_id,),
    )
    return rows[0] if rows else None


def _set_membership(clinic_id: str, plan_id: str, status: str, *, period_end: str | None = None) -> None:
    store = get_dental_store()
    now = str(int(time.time()))
    current = _membership(clinic_id)
    if current:
        sql = (
            "UPDATE dental_memberships SET plan_id=%s,status=%s,current_period_end=%s,cancelled_at=%s,updated_at=%s WHERE id=%s"
            if store.database_url else
            "UPDATE dental_memberships SET plan_id=?,status=?,current_period_end=?,cancelled_at=?,updated_at=? WHERE id=?"
        )
        cancelled_at = now if status in {"cancelled", "refunded"} else None
        store._execute(sql, (plan_id, status, period_end, cancelled_at, now, current["id"]))
    else:
        sql = (
            "INSERT INTO dental_memberships(id,clinic_id,plan_id,status,started_at,current_period_end,cancelled_at,updated_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            if store.database_url else
            "INSERT INTO dental_memberships(id,clinic_id,plan_id,status,started_at,current_period_end,cancelled_at,updated_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)"
        )
        store._execute(sql, (str(uuid.uuid4()), clinic_id, plan_id, status, now, period_end, now if status in {"cancelled", "refunded"} else None, now, now))


async def create_order(clinic_id: str, plan_id: str, actor_id: str) -> dict:
    if clinic_id and clinic_id == os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip():
        return {"free": True, "plan": {"id": "master", "name": "Dr. Pranali Master", "price_inr": 0, "period": "forever"}}
    plan = _plan(plan_id)
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise HTTPException(503, "Online membership checkout is not configured yet")
    receipt = "dental_" + uuid.uuid4().hex[:20]
    payload = {"amount": plan["price_inr"] * 100, "currency": "INR", "description": f"Dental Platform {plan['name']} membership",
               "reference_id": receipt, "expire_by": int(time.time()) + 86400,
               "notes": {"clinic_id": clinic_id, "plan_id": plan_id, "actor_id": actor_id}}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post("https://api.razorpay.com/v1/payment_links", auth=(key_id, key_secret), json=payload)
    if response.status_code >= 400:
        raise HTTPException(502, "Razorpay could not create the membership order")
    order = response.json()
    store = get_dental_store()
    store._execute(
        "INSERT INTO dental_membership_orders(id,clinic_id,plan_id,razorpay_order_id,amount_inr,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        if store.database_url else
        "INSERT INTO dental_membership_orders(id,clinic_id,plan_id,razorpay_order_id,amount_inr,status,created_at) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), clinic_id, plan_id, order["id"], plan["price_inr"], "created", str(int(time.time()))),
    )
    return {"free": False, "plan": plan, "razorpay": {"key_id": key_id, "order_id": order["id"], "short_url": order.get("short_url"), "amount": order["amount"], "currency": order["currency"]}}


def _extract(event: dict) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    payload = event.get("payload") or {}
    payment = (payload.get("payment") or {}).get("entity") or {}
    payment_link = (payload.get("payment_link") or {}).get("entity") or {}
    order = (payload.get("order") or {}).get("entity") or {}
    refund = (payload.get("refund") or {}).get("entity") or {}
    subscription = (payload.get("subscription") or {}).get("entity") or {}
    order_id = payment_link.get("id") or payment.get("order_id") or order.get("id") or subscription.get("id")
    payment_id = payment.get("id")
    plan_id = ((subscription.get("notes") or {}).get("plan_id") or subscription.get("plan_id")
               or (payment_link.get("notes") or {}).get("plan_id"))
    clinic_id = ((subscription.get("notes") or {}).get("clinic_id")
                 or (payment_link.get("notes") or {}).get("clinic_id")
                 or (order.get("notes") or {}).get("clinic_id"))
    return order_id, payment_id, plan_id, clinic_id, refund.get("id")


def _find_order(order_id: str | None):
    if not order_id:
        return None
    store = get_dental_store()
    rows = store._execute(
        "SELECT * FROM dental_membership_orders WHERE razorpay_order_id=%s" if store.database_url else
        "SELECT * FROM dental_membership_orders WHERE razorpay_order_id=?",
        (order_id,),
    )
    return rows[0] if rows else None


def _update_order(order_id: str | None, status: str, payment_id: str | None = None) -> None:
    if not order_id:
        return
    store = get_dental_store()
    sql = (
        "UPDATE dental_membership_orders SET status=%s,razorpay_payment_id=COALESCE(%s,razorpay_payment_id),paid_at=%s WHERE razorpay_order_id=%s"
        if store.database_url else
        "UPDATE dental_membership_orders SET status=?,razorpay_payment_id=COALESCE(?,razorpay_payment_id),paid_at=? WHERE razorpay_order_id=?"
    )
    store._execute(sql, (status, payment_id, str(int(time.time())) if status == "paid" else None, order_id))


def handle_webhook(raw_body: bytes, signature: str) -> dict:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "Razorpay webhook secret is not configured")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise HTTPException(401, "Invalid Razorpay webhook signature")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid Razorpay webhook body") from exc

    event_name = str(event.get("event") or "")
    event_id = str(event.get("id") or hashlib.sha256(raw_body).hexdigest())
    store = get_dental_store()
    existing = store._execute(
        "SELECT event_id FROM dental_razorpay_events WHERE event_id=%s" if store.database_url else
        "SELECT event_id FROM dental_razorpay_events WHERE event_id=?",
        (event_id,),
    )
    if existing:
        return {"ok": True, "duplicate": True, "event_id": event_id}

    # Record the verified event before applying entitlement changes. The unique event id
    # prevents a replay from being applied twice.
    store._execute(
        "INSERT INTO dental_razorpay_events(event_id,event_name,processed_at) VALUES (%s,%s,%s)" if store.database_url else
        "INSERT INTO dental_razorpay_events(event_id,event_name,processed_at) VALUES (?,?,?)",
        (event_id, event_name, str(int(time.time()))),
    )

    order_id, payment_id, event_plan, event_clinic, _refund_id = _extract(event)
    order = _find_order(order_id)
    clinic_id = event_clinic or (order or {}).get("clinic_id")
    plan_id = event_plan or (order or {}).get("plan_id")

    if event_name in {"payment.captured", "order.paid", "payment_link.paid"}:
        if order and clinic_id and plan_id:
            _update_order(order_id, "paid", payment_id)
            _set_membership(clinic_id, plan_id, "active", period_end=_period_end())
    elif event_name in {"payment.failed", "order.failed", "payment_link.failed"}:
        _update_order(order_id, "failed", payment_id)
        current = _membership(clinic_id) if clinic_id else None
        if current and current.get("status") == "active":
            _set_membership(clinic_id, current["plan_id"], "past_due", period_end=current.get("current_period_end"))
    elif event_name in {"refund.created", "refund.processed", "payment.refunded"}:
        _update_order(order_id, "refunded", payment_id)
        if clinic_id and plan_id:
            _set_membership(clinic_id, plan_id, "refunded", period_end=None)
    elif event_name in {"subscription.cancelled", "subscription.canceled"}:
        if clinic_id and plan_id:
            _set_membership(clinic_id, plan_id, "cancelled", period_end=_membership(clinic_id).get("current_period_end") if _membership(clinic_id) else None)
    elif event_name in {"subscription.charged", "subscription.activated", "subscription.resumed"}:
        if clinic_id and plan_id:
            current = _membership(clinic_id)
            _set_membership(clinic_id, plan_id, "active", period_end=_period_end(current.get("current_period_end") if current else None))
    elif event_name in {"subscription.paused"}:
        if clinic_id and plan_id:
            _set_membership(clinic_id, plan_id, "paused", period_end=_membership(clinic_id).get("current_period_end") if _membership(clinic_id) else None)
    elif event_name in {"subscription.updated", "subscription.plan_changed"}:
        if clinic_id and plan_id:
            _plan(plan_id)
            _set_membership(clinic_id, plan_id, "active", period_end=_membership(clinic_id).get("current_period_end") if _membership(clinic_id) else _period_end())

    return {"ok": True, "event_id": event_id, "event": event_name, "entitlement_status": _membership(clinic_id).get("status") if clinic_id and _membership(clinic_id) else None}
