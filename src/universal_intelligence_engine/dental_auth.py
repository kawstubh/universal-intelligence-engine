"""Production authentication for the Dental Doctor App."""
from __future__ import annotations
import hashlib, os, secrets, time
import httpx
import base64
from fastapi import Header, HTTPException
from .dental_store import get_dental_store

DOCTOR_PHONE = os.getenv("DENTAL_DOCTOR_PHONE", "9820373350")
DEMO_AUTH = os.getenv("DENTAL_DEMO_AUTH", "false").lower() == "true"
DEMO_OTP = os.getenv("DENTAL_DEMO_OTP", "123456")
OTP_TTL = 300
SESSION_TTL = 60 * 60 * 24 * 30

def _phone(v: str) -> str:
    digits = "".join(c for c in str(v) if c.isdigit())
    if digits.startswith("91") and len(digits) == 12: digits = digits[2:]
    if len(digits) != 10: raise HTTPException(400, "Enter a valid 10-digit mobile number")
    return digits

def _hash(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()

_PASSWORD_ITERATIONS = 310_000

def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"

def _password_verify(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256": return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False

async def login_password(email: str, password: str) -> dict:
    email = email.strip().lower()
    if not email or not password:
        raise HTTPException(400, "Email and password are required")
    doctor = get_dental_store().doctor_by_email(email)
    if not doctor or not _password_verify(password, doctor["password_hash"]):
        raise HTTPException(401, "Invalid clinic login credentials")
    token = secrets.token_urlsafe(48)
    get_dental_store().create_session(doctor["id"], _hash(token), int(time.time()) + SESSION_TTL)
    return {"access_token": token, "expires_in": SESSION_TTL, "user": {"id": doctor["id"], "email": doctor["email"], "phone": doctor.get("phone", ""), "role": doctor.get("role", "doctor"), "clinic_id": doctor.get("clinic_id", "dr-pranali")}}

async def login_google(access_token: str) -> dict:
    """Exchange a Supabase Google session for the Dental API's short-lived doctor session."""
    token = (access_token or "").strip()
    if not token:
        raise HTTPException(400, "Google access token is required")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    publishable_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    allowed_email = os.getenv("DENTAL_GOOGLE_ALLOWED_EMAIL", "").strip().lower()
    if not supabase_url or not publishable_key:
        raise HTTPException(503, "Google authentication is not configured on the Dental API")
    if not allowed_email:
        raise HTTPException(503, "Set DENTAL_GOOGLE_ALLOWED_EMAIL for the clinic doctor account")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_url}/auth/v1/user",
            headers={"apikey": publishable_key, "Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(401, "Invalid Google/Supabase session")
    profile = response.json()
    email = str(profile.get("email") or "").strip().lower()
    if email != allowed_email:
        raise HTTPException(403, "This Google account is not authorized for the doctor workspace")
    doctor = get_dental_store().doctor_by_email(email)
    if not doctor:
        raise HTTPException(403, "Authorized Google account is not registered as a clinic doctor")
    session_token = secrets.token_urlsafe(48)
    doctor_id = "google-" + str(profile.get("id") or doctor["id"])
    get_dental_store().create_session(doctor_id, _hash(session_token), int(time.time()) + SESSION_TTL)
    return {
        "access_token": session_token,
        "expires_in": SESSION_TTL,
        "user": {
            "id": doctor_id,
            "email": email,
            "phone": doctor.get("phone", ""),
            "role": doctor.get("role", "doctor"),
            "clinic_id": doctor.get("clinic_id", "dr-pranali"),
        },
    }

async def request_otp(phone: str) -> dict:
    phone = _phone(phone)
    if phone != DOCTOR_PHONE:
        raise HTTPException(403, "This mobile number is not registered for a doctor account")
    store = get_dental_store()
    now = int(time.time())
    recent = store.latest_otp(phone)
    if recent and now - int(recent["created_at"]) < 30:
        raise HTTPException(429, "Please wait 30 seconds before requesting another OTP")
    code = DEMO_OTP if DEMO_AUTH else f"{secrets.randbelow(1000000):06d}"
    challenge = secrets.token_urlsafe(18)
    store.create_otp(phone, _hash(code), challenge, now, now + OTP_TTL)
    # Demo mode is temporary and must be explicitly enabled by a server-side environment flag.
    if DEMO_AUTH:
        return {"challenge_id": challenge, "expires_in": OTP_TTL, "message": "Demo OTP ready"}
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
    # Temporary clinic demo bridge: enabled only by an explicit server-side flag.
    # This lets the packaged Doctor APK exercise the real shared backend while
    # production OTP delivery is being configured. It is never enabled by default.
    if DEMO_AUTH and secrets.compare_digest(token, "demo"):
        return {"id": "doctor-demo", "phone": DOCTOR_PHONE, "role": "doctor", "demo": True}
    row = get_dental_store().get_session(_hash(token))
    if not row or int(row["expires_at"]) < int(time.time()) or row.get("revoked"):
        raise HTTPException(401, "Invalid or expired doctor session")
    return {"id": row["doctor_id"], "phone": row["doctor_id"].replace("doctor-", ""), "role": "doctor"}
