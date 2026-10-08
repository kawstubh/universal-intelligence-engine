import hashlib
import hmac
import json
import time

from fastapi import HTTPException

from universal_intelligence_engine import dental_api, dental_store
from universal_intelligence_engine.dental_billing import handle_webhook
from universal_intelligence_engine.dental_store import get_dental_store


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_SQLITE_PATH", str(tmp_path / "razorpay.sqlite3"))
    monkeypatch.setenv("DENTAL_AUTO_MIGRATE", "true")
    monkeypatch.setenv("DENTAL_MASTER_CLINIC_ID", "master")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "webhook-secret")
    dental_api._store = None
    dental_store._store = None
    return get_dental_store()


def _seed_order(store, *, order_id="plink_test_1", plan_id="clinic", status="created"):
    store._execute(
        "INSERT INTO dental_membership_orders(id,clinic_id,plan_id,razorpay_order_id,amount_inr,status,created_at) VALUES (?,?,?,?,?,?,?)",
        ("order-" + order_id, "clinic-a", plan_id, order_id, 4999, status, str(int(time.time()))),
    )


def _event(event_id, event_name, *, order_id="plink_test_1", plan_id="clinic", clinic_id="clinic-a", payment_id="pay_test_1"):
    entity = {
        "id": order_id,
        "notes": {"clinic_id": clinic_id, "plan_id": plan_id},
        "payments": [{"payment_id": payment_id}],
    }
    return {
        "id": event_id,
        "event": event_name,
        "payload": {
            "payment_link": {"entity": entity},
            "payment": {"entity": {"id": payment_id, "order_id": order_id}},
            "order": {"entity": {"id": order_id, "notes": {"clinic_id": clinic_id, "plan_id": plan_id}}},
            "refund": {"entity": {"id": "rf_test_1"}},
            "subscription": {"entity": {"id": order_id, "plan_id": plan_id, "notes": {"clinic_id": clinic_id, "plan_id": plan_id}}},
        },
    }


def _signed(event):
    body = json.dumps(event, separators=(",", ":")).encode()
    sig = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    return body, sig


def _status(store):
    rows = store._execute("SELECT plan_id,status,current_period_end,cancelled_at FROM dental_memberships WHERE clinic_id=? ORDER BY created_at DESC LIMIT 1", ("clinic-a",))
    return rows[0] if rows else None


def test_bad_signature_is_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    body, _ = _signed(_event("evt_bad", "payment.captured"))
    try:
        handle_webhook(body, "bad")
        assert False
    except HTTPException as exc:
        assert exc.status_code == 401


def test_payment_success_and_replay_are_idempotent(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store)
    body, sig = _signed(_event("evt_paid", "payment_link.paid"))
    first = handle_webhook(body, sig)
    second = handle_webhook(body, sig)
    assert first["entitlement_status"] == "active"
    assert second["duplicate"] is True
    assert _status(store)["status"] == "active"
    assert len(store._execute("SELECT * FROM dental_memberships WHERE clinic_id=?", ("clinic-a",))) == 1


def test_payment_failed_does_not_activate_and_marks_existing_membership_past_due(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store, order_id="plink_failed")
    body, sig = _signed(_event("evt_failed", "payment.failed", order_id="plink_failed"))
    result = handle_webhook(body, sig)
    assert result["entitlement_status"] is None
    order = store._execute("SELECT status FROM dental_membership_orders WHERE razorpay_order_id=?", ("plink_failed",))[0]
    assert order["status"] == "failed"

    _seed_order(store, order_id="plink_active")
    paid, paid_sig = _signed(_event("evt_active", "payment_link.paid", order_id="plink_active"))
    handle_webhook(paid, paid_sig)
    failed2, failed2_sig = _signed(_event("evt_failed2", "payment.failed", order_id="plink_active"))
    assert handle_webhook(failed2, failed2_sig)["entitlement_status"] == "past_due"


def test_refund_revokes_entitlement(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store)
    paid, paid_sig = _signed(_event("evt_refund_paid", "payment_link.paid"))
    handle_webhook(paid, paid_sig)
    refund, refund_sig = _signed(_event("evt_refund", "payment.refunded"))
    result = handle_webhook(refund, refund_sig)
    assert result["entitlement_status"] == "refunded"
    assert _status(store)["cancelled_at"] is not None


def test_cancellation_revokes_entitlement(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store)
    paid, paid_sig = _signed(_event("evt_cancel_paid", "payment_link.paid"))
    handle_webhook(paid, paid_sig)
    cancel, cancel_sig = _signed(_event("evt_cancel", "subscription.cancelled"))
    assert handle_webhook(cancel, cancel_sig)["entitlement_status"] == "cancelled"


def test_renewal_reactivates_and_extends_entitlement(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store)
    paid, paid_sig = _signed(_event("evt_renew_paid", "payment_link.paid"))
    handle_webhook(paid, paid_sig)
    before = _status(store)["current_period_end"]
    renew, renew_sig = _signed(_event("evt_renew", "subscription.charged"))
    result = handle_webhook(renew, renew_sig)
    assert result["entitlement_status"] == "active"
    assert _status(store)["current_period_end"] != before


def test_plan_change_updates_entitlement_plan(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch)
    _seed_order(store)
    paid, paid_sig = _signed(_event("evt_plan_paid", "payment_link.paid"))
    handle_webhook(paid, paid_sig)
    changed, changed_sig = _signed(_event("evt_plan_change", "subscription.updated", plan_id="enterprise"))
    result = handle_webhook(changed, changed_sig)
    assert result["entitlement_status"] == "active"
    assert _status(store)["plan_id"] == "enterprise"
