"""Shared PostgreSQL-backed rate limiting for Oralyn authentication and intake.

The counter is an atomic fixed-window upsert, so multiple application workers
and instances observe the same limit. SQLite support exists for deterministic
local tests only; production/staging PostgreSQL must have the signing key set.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import HTTPException


def enforce_shared_rate_limit(
    store,
    *,
    scope: str,
    client_identity: str,
    max_attempts: int,
    window_seconds: int,
    now: int | None = None,
) -> None:
    """Atomically count an attempt in the shared database window.

    Only a keyed digest is persisted; raw client IPs are never stored.
    Missing key/schema/database availability fails closed with HTTP 503.
    """
    if not scope or max_attempts < 1 or window_seconds < 1:
        raise ValueError("Invalid rate-limit policy")

    secret = (
        os.getenv("DENTAL_RATE_LIMIT_HASH_KEY", "").strip()
        or os.getenv("DENTAL_PUBLIC_BOOKING_HASH_KEY", "").strip()
    )
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RATE_LIMIT_NOT_CONFIGURED",
                "message": "This service is temporarily unavailable. Please try again shortly.",
            },
            headers={"Retry-After": str(window_seconds)},
        )

    identity = (client_identity or "unknown").strip()[:128] or "unknown"
    material = f"oralyn-rate-limit:v1:{scope}:{identity}".encode("utf-8")
    key_hash = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    current = int(time.time()) if now is None else int(now)
    window_start = current - (current % window_seconds)

    postgres = bool(store.database_url)
    placeholder = "%s" if postgres else "?"
    try:
        # Keep the table bounded to one day of active client/scope keys.
        store._execute(
            f"DELETE FROM dental_rate_limit_buckets WHERE window_start < {placeholder}",
            (current - 86_400,),
        )
        sql = (
            "INSERT INTO dental_rate_limit_buckets "
            "(key_hash,window_start,attempt_count,updated_at) "
            f"VALUES ({placeholder},{placeholder},1,{placeholder}) "
            "ON CONFLICT(key_hash) DO UPDATE SET "
            "attempt_count=CASE "
            "WHEN dental_rate_limit_buckets.window_start=excluded.window_start "
            "THEN dental_rate_limit_buckets.attempt_count+1 ELSE 1 END, "
            "window_start=excluded.window_start, updated_at=excluded.updated_at "
            "RETURNING attempt_count,window_start"
        )
        rows = store._execute(sql, (key_hash, window_start, current))
    except Exception as exc:
        # Do not silently fall back to process-local enforcement when the
        # shared store is unavailable or the migration has not been applied.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RATE_LIMIT_STORE_UNAVAILABLE",
                "message": "This service is temporarily unavailable. Please try again shortly.",
            },
            headers={"Retry-After": str(window_seconds)},
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RATE_LIMIT_STORE_UNAVAILABLE",
                "message": "This service is temporarily unavailable. Please try again shortly.",
            },
            headers={"Retry-After": str(window_seconds)},
        )

    attempts = int(rows[0]["attempt_count"])
    if attempts > max_attempts:
        retry_after = max(1, window_start + window_seconds - current)
        code = f"{scope.upper()}_RATE_LIMITED"
        raise HTTPException(
            status_code=429,
            detail={
                "code": code,
                "message": "Too many requests. Please wait and try again.",
            },
            headers={"Retry-After": str(retry_after)},
        )
