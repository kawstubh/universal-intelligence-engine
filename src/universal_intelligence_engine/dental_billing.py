"""Dental SaaS membership catalog and billing helpers."""
from __future__ import annotations
import hashlib
import hmac
import os
import time
import uuid
import httpx
from fastapi import HTTPException
from .dental_store import get_dental_store

PLANS = [
    {
        "id": "starter",
        "name": "Starter",
        "price_inr": 999,
        "period": "month",
        "tagline": "For a solo clinic starting digital operations",
        "features": ["1 doctor", "Appointments", "Patient records", "Basic dental chart", "WhatsApp-ready workflows"],
    },
    {
        "id": "professional",
        "name": "Professional",
        "price_inr": 2499,
        "period": "month",
        "tagline": "For growing clinics that need the full workflow",
        "features": ["Up to 3 doctors", "All Starter features", "Dental chart + periodontogram", "Treatment planning", "AI clinical workspace", "Reports"],
    },
    {
        "id": "clinic",
        "name": "Clinic",
        "price_inr": 4999,
        "period": "month",
        "tagline": "For multi-doctor clinics and advanced operations",
        "features": ["Up to 10 doctors", "All Professional features", "Staff roles", "Advanced AI modules", "Scan/WhatsApp integrations", "Priority support"],
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "price_inr": 9999,
        "period": "month",
        "tagline": "For groups, chains and custom requirements",
        "features": ["Unlimited clinic staff", "Multi-location support", "Custom integrations", "Dedicated onboarding", "Custom limits and SLA"],
    },
]

def plans() -> list[dict]:
    return PLANS

def _plan(plan_id: str) -> dict:
    for p in PLANS:
        if p["id"] == plan_id:
            return p
    raise HTTPException(404, "Membership plan not found")

def _master_clinic() -> bool:
    return os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip() == os.getenv("DENTAL_CURRENT_CLINIC_ID", "").strip()

async def create_order(clinic_id: str, plan_id: str, actor_id: str) -> dict:
    if clinic_id and clinic_id == os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip():
        return {"free": True, "plan": {"id": "master", "name": "Dr. Pranali Master", "price_inr": 0, "period": "forever"}}
    plan = _plan(plan_id)
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise HTTPException(503, "Online membership checkout is not configured yet")
    receipt = "dental_" + uuid.uuid4().hex[:20]
    payload = {
        "amount": plan["price_inr"] * 100,
        "currency": "INR",
        'description': f"Dental Platform {plan['name']} membership",
        "reference_id": receipt,
        "expire_by": int(time.time()) + 86400,
        "notes": {"clinic_id": clinic_id, "plan_id": plan_id, "actor_id": actor_id},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=(key_id, key_secret),
            json=payload,
        )
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
    return {
        "free": False,
        "plan": plan,
        "razorpay": {"key_id": key_id, "order_id": order["id"], "short_url": order.get("short_url"), "amount": order["amount"], "currency": order["currency"]},
    }

def handle_webhook(raw_body: bytes, signature: str) -> dict:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "Razorpay webhook secret is not configured")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise HTTPException(401, "Invalid Razorpay webhook signature")
    import json
    event = json.loads(raw_body.decode("utf-8"))
    event_name = event.get("event", "")
    if event_name in {"payment.captured", "order.paid", "payment_link.paid"}:
        payment = event.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id") or event.get("payload", {}).get("order", {}).get("entity", {}).get("id") or event.get("payload", {}).get("payment_link", {}).get("entity", {}).get("id")
        if order_id:
            store = get_dental_store()
            rows = store._execute(
                "SELECT * FROM dental_membership_orders WHERE razorpay_order_id=%s" if store.database_url else
                "SELECT * FROM dental_membership_orders WHERE razorpay_order_id=?",
                (order_id,),
            )
            if rows:
                row = rows[0]
                store._execute(
                    "UPDATE dental_membership_orders SET status='paid', razorpay_payment_id=%s, paid_at=%s WHERE razorpay_order_id=%s"
                    if store.database_url else
                    "UPDATE dental_membership_orders SET status='paid', razorpay_payment_id=?, paid_at=? WHERE razorpay_order_id=?",
                    (payment.get("id"), str(int(time.time())), order_id),
                )
                store._execute(
                    "INSERT INTO dental_memberships(id,clinic_id,plan_id,status,started_at,created_at) VALUES (%s,%s,%s,%s,%s,%s)"
                    if store.database_url else
                    "INSERT INTO dental_memberships(id,clinic_id,plan_id,status,started_at,created_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), row["clinic_id"], row["plan_id"], "active", str(int(time.time())), str(int(time.time()))),
                )
    return {"ok": True}
