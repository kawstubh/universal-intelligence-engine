"""scanO integration endpoints.

The live vendor transport is intentionally kept outside this API until scanO
provides approved sandbox credentials and the authenticated endpoint contract.
This endpoint validates/normalizes a scanO response for downstream workflows.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .scano_adapter import ScanOAdapter

router = APIRouter(prefix="/v1/integrations/scano", tags=["scanO"])


class ScanONormalizeBody(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


def _authorize(authorization: str | None) -> None:
    import os
    expected = os.getenv("UIE_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="UIE_API_KEY is not configured; API access is fail-closed.")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API credentials")


@router.post("/normalize")
def normalize_scano(
    body: ScanONormalizeBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    result = ScanOAdapter().normalize(body.payload)
    return {
        "source": result.source,
        "scan_id": result.scan_id,
        "signals": [
            {
                "condition": signal.condition,
                "risk": signal.risk,
                "confidence": signal.confidence,
                "evidence": signal.evidence,
            }
            for signal in result.signals
        ],
        "provenance": {
            "vendor": "scanO",
            "raw_payload_preserved": True,
            "clinical_reinterpretation": False,
        },
    }
