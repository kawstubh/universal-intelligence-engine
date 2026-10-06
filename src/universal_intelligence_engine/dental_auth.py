"""Production OTP/session authentication for the Dental Doctor App."""
from __future__ import annotations
import hashlib, os, secrets, time
from fastapi import Header, HTTPException
from .dental_store import get_dental_store

DOCTOR_PHONE = os.getenv("DENTAL_DOCTOR_PHONE", "9820373350")
OTP_TTL = 300
SESSION_TTL = 60 * 60 * 24 * 30

def _phone(v: str) -> str:
    digits = "".join(c for c in str(v) if c.isdigit())
    if digits.startswith("91") and len(digits) == 12: digits = digits[2:]
    if len(digits) != 10: raise HTTPException(400, "Enter a valid 10-digit mobile number")
    return digits

def _hash(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()

async def request_otp(phone: str) -> dict:
    phone = _phone(phone)
    if phone != DOCTOR_PHONE:
        raise HTTPException(403, "This mobile number is not registered for a doctor account")
    store = get_dental_store()
    now = int(time.time())
    recent = store.latest_otp(phone)
    if recent and now - int(recent["created_at"]) < 30:
        raise HTTPException(429, "Please wait 30 seconds before requesting another OTP")
    code = f"{secrets.randbelow(1000000):06d}"
    challenge = secrets.token_urlsafe(18)
    store.create_otp(phone, _hash(code), challenge, now, now + OTP_TTL)
    # Production delivery uses the configured official SMS/WhatsApp gateway.
    delivered = await _deliver_otp(phone, code)
    if not delivered:
        raise HTTPException(503, "OTP delivery is not configured yet. Add the clinic SMS/WhatsApp provider credentials.")
    return {"challenge_id": challenge, "expires_in": OTP_TTL, "message": "OTP sent"}

async def _deliver_otp(phone: str, code: str) -> bool:
    # Provider-neutral production hook. No OTP is returned to the app.
    url = os.getenv("OTP_SMS_WEBHOOK_URL")
    if not url:
        return False
    import httpx
    payload = {"to": phone, "otp": code, "message": f"Your Dr. Pranali Dental verification code is {code}. It expires in 5 minutes."}
    secret = os.getenv("OTP_SMS_WEBHOOK_SECRET")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
    return 200 <= r.status_code < 300

async def verify_otp(phone: str, challenge_id: str, otp: str) -> dict:
    phone = _phone(phone)
    row = get_dental_store().get_otp(phone, challenge_id)
    if not row or int(row["expires_at"]) < int(time.time()) or int(row["attempts"]) >= 5:
        raise HTTPException(401, "OTP expired or invalid")
    if not secrets.compare_digest(_hash(otp.strip()), row["otp_hash"]):
        get_dental_store().increment_otp_attempt(row["id"])
        raise HTTPException(401, "Incorrect OTP")
    token = secrets.token_urlsafe(48)
    get_dental_store().create_session(phone, _hash(token), int(time.time()) + SESSION_TTL)
    get_dental_store().consume_otp(row["id"])
    return {"access_token": token, "expires_in": SESSION_TTL, "user": {"id": "doctor-" + phone, "phone": phone, "role": "doctor"}}

async def require_doctor(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer access token required")
    token = authorization[7:]
    row = get_dental_store().get_session(_hash(token))
    if not row or int(row["expires_at"]) < int(time.time()) or row.get("revoked"):
        raise HTTPException(401, "Invalid or expired doctor session")
    return {"id": row["doctor_id"], "phone": row["doctor_id"].replace("doctor-", ""), "role": "doctor"}
