"""Provider-neutral scanO/scanKit adapter.

Live mode is intentionally opt-in. No unofficial reverse engineering or fake
provider calls are performed. Configure the official scanO API credentials
when access is granted.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class ScanOAdapter:
    def __init__(self) -> None:
        self.mode = os.getenv("SCANO_MODE", "disabled").lower()
        self.base_url = os.getenv("SCANO_API_URL", "").rstrip("/")
        self.api_key = os.getenv("SCANO_API_KEY", "")

    @property
    def configured(self) -> bool:
        return self.mode == "live" and bool(self.base_url and self.api_key)

    async def screen(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "mock":
            return {
                "provider": "mock",
                "status": "sandbox",
                "risk_score": None,
                "evidence": [],
                "report": {
                    "message": "Mock scan response. Official scanO access is not connected."
                },
                "source": "mock",
            }
        if not self.configured:
            raise RuntimeError("Official scanO/scanKit API access is not configured")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"scanO API returned HTTP {response.status_code}")
        return response.json()


def get_scano_adapter() -> ScanOAdapter:
    return ScanOAdapter()
