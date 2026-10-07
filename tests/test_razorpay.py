import hashlib
import hmac
import json
import time
from fastapi import HTTPException

from universal_intelligence_engine import dental_api
from universal_intelligence_engine.dental_billing import handle_webhook
from universal_intelligence_engine.dental_store import get_dental_store


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "razorpay.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "webhook-secret")
    dental_api._store = None
    return get_dental_store()


def _event(order_id="plink_test_1"):
    return {
        "id": "evt_test_1",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": order_id,
                    "payments": [{"payment_id": "pay_test_1"}],
                }
            },
            "payment": {"entity": {"id": "pay_test_1"}},
        },
    }


def test_razorpay_rejects_bad_signature(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    body = json.dumps(_event()).encode()
    try:
        handle_webhook(body, "bad")
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_razorpay_signature_idempotency_and_entitlement(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    now = str(int(time.time()))
    store._execute(
        "INSERT INTO dental_membership_orders(id,clinic_id,plan_id,razorpay_order_id,amount_inr,status,created_at) VALUES (?,?,?,?,?,?,?)",
        ("order-1", "clinic-a", "clinic", "plink_test_1", 4999, "created", now),
    )
    body = json.dumps(_event()).encode()
    signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

    first = handle_webhook(body, signature)
    second = handle_webhook(body, signature)

    assert first["ok"] is True
    assert first["event_id"] == "evt_test_1"
    assert second["duplicate"] is True
    orders = store._execute("SELECT status,razorpay_payment_id FROM dental_membership_orders WHERE id=?", ("order-1",))
    memberships = store._execute("SELECT clinic_id,plan_id,status FROM dental_memberships WHERE clinic_id=?", ("clinic-a",))
    events = store._execute("SELECT event_id FROM dental_razorpay_events WHERE event_id=?", ("evt_test_1",))
    assert orders[0]["status"] == "paid"
    assert orders[0]["razorpay_payment_id"] == "pay_test_1"
    assert len(memberships) == 1
    assert memberships[0]["status"] == "active"
    assert len(events) == 1
