"""WhatsApp Business Platform adapter.

Uses the official Meta Cloud API only. Credentials are environment variables;
no credentials are stored in the app or database.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class WhatsAppAdapter:
    def __init__(self) -> None:
        self.token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.api_version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
        self.enabled = os.getenv("WHATSAPP_MODE", "disabled").lower() == "live"

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.token and self.phone_number_id)

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Official WhatsApp Business API is not configured")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"WhatsApp API returned HTTP {response.status_code}")
        return response.json()


def get_whatsapp_adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter()
