"""Direct Sarvam chat-completions adapter for the temporary Dental staging route."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import HTTPException

SARVAM_CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v1/chat/completions"
DEFAULT_SARVAM_MODEL = "sarvam-105b"
_BLOCKED_KEYS = {"patient_id", "id", "name", "full_name", "phone", "mobile", "email", "address"}


def redact_direct_identifiers(value: Any) -> Any:
    """Recursively remove direct patient identifiers before external transmission."""
    if isinstance(value, dict):
        return {
            str(key): redact_direct_identifiers(item)
            for key, item in value.items()
            if str(key).lower() not in _BLOCKED_KEYS
        }
    if isinstance(value, list):
        return [redact_direct_identifiers(item) for item in value]
    return value


async def generate_sarvam_completion(
    capability: str,
    goal: str,
    context: dict[str, Any],
) -> str:
    """Generate a clinician decision-support response without writing records."""
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            503,
            detail={"message": "Clinical AI provider is not configured.", "code": "SARVAM_NOT_CONFIGURED"},
        )

    model = os.getenv("DENTAL_SARVAM_MODEL", DEFAULT_SARVAM_MODEL).strip() or DEFAULT_SARVAM_MODEL
    safe_context = redact_direct_identifiers(context)
    prompt = (
        f"Clinical capability: {capability}\n"
        f"Task: {goal}\n"
        "Use only the supplied clinical facts. Do not invent findings, diagnoses, "
        "medicines, dosages, or procedures. Separate documented facts from "
        "inferences and uncertainty. Cite guideline/evidence sources only when "
        "you can identify them reliably; otherwise say that sources were not verified. "
        "This is decision support for a licensed dentist, not an autonomous decision. "
        "Do not write to or modify any patient record. Return a concise, structured answer.\n\n"
        f"Authorized clinical context (direct identifiers removed): {json.dumps(safe_context, ensure_ascii=False)}"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                SARVAM_CHAT_COMPLETIONS_URL,
                headers={
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a cautious dental clinical decision-support assistant. The dentist makes all clinical decisions.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 3000,
                    "temperature": 0.2,
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            detail={"message": "Clinical AI timed out. Please retry.", "code": "SARVAM_TIMEOUT"},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            detail={"message": "Clinical AI provider could not be reached.", "code": "SARVAM_UNREACHABLE"},
        ) from exc

    if response.status_code >= 400:
        status = 503 if response.status_code in (401, 403) else 429 if response.status_code == 429 else 502
        raise HTTPException(
            status,
            detail={"message": "Clinical AI provider returned an error.", "code": "SARVAM_PROVIDER_ERROR"},
        )
    try:
        payload = response.json()
        answer = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(
            502,
            detail={"message": "Clinical AI returned an invalid response.", "code": "SARVAM_BAD_RESPONSE"},
        ) from exc
    if not isinstance(answer, str) or not answer.strip():
        raise HTTPException(
            502,
            detail={"message": "Clinical AI returned an empty response.", "code": "SARVAM_EMPTY_RESPONSE"},
        )
    return answer.strip()
