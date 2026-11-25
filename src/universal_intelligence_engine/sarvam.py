"""Sarvam Token Factory reasoning adapter for UIE.

The provider is opt-in through UIE_REASONING_PROVIDER=sarvam. Credentials are
read only from SARVAM_API_KEY and are never returned by health or logs.
"""
from __future__ import annotations

import os
from typing import Sequence

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .providers import _json_request


class SarvamTokenFactoryReasoningProvider:
    """Evidence-aware chat-completions adapter for Sarvam Token Factory."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("SARVAM_API_KEY")
        self.model = model or os.getenv("SARVAM_MODEL", "sarvam-105b")
        self.base_url = (base_url or os.getenv(
            "SARVAM_BASE_URL", "https://api.sarvam.ai/v2"
        )).rstrip("/")
        configured_tokens = max_tokens or int(os.getenv("SARVAM_MAX_TOKENS", "512"))
        self.max_tokens = max(64, min(configured_tokens, 2048))

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
    ) -> IntelligenceResponse:
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY is not configured")

        evidence_block = "\n\n".join(
            f"[{idx}] {item.title}\nSource: {item.source}\n"
            f"URL: {item.url or 'n/a'}\n{item.content}"
            for idx, item in enumerate(evidence, start=1)
        )
        system = (
            "You are the reasoning layer of UIE. Use supplied evidence and context; "
            "distinguish supported facts from uncertainty; never invent sources or "
            "citations. If evidence is insufficient, state the limitation. Follow "
            "the requested language and all constraints. For clinical topics, provide "
            "decision support for a qualified clinician, not a diagnosis or substitute "
            "for professional judgement. Never claim to have changed a patient record."
        )
        user = (
            f"Goal: {request.goal}\n"
            f"Language: {request.language or 'en'}\n"
            f"Locale: {request.locale or 'global'}\n"
            f"Context: {__import__('json').dumps(dict(request.context), ensure_ascii=False)}\n"
            f"Constraints: {__import__('json').dumps(dict(request.constraints), ensure_ascii=False)}\n\n"
            f"Evidence:\n{evidence_block or 'No evidence was retrieved.'}"
        )
        data = _json_request(
            f"{self.base_url}/chat/completions",
            method="POST",
            headers={"api-subscription-key": self.api_key},
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": self.max_tokens,
                "temperature": 0.2,
            },
            timeout=float(os.getenv("SARVAM_TIMEOUT_SECONDS", "60")),
        )
        choices = data.get("choices")
        message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
        answer = message.get("content") if isinstance(message, dict) else None
        if isinstance(answer, list):
            answer = "\n".join(
                str(part.get("text", "")) for part in answer if isinstance(part, dict)
            ).strip()
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Sarvam returned no completion text")

        return IntelligenceResponse(
            answer=answer.strip(),
            evidence=evidence,
            confidence=None,
            metadata={
                "provider": "sarvam-token-factory",
                "model": self.model,
                "response_id": data.get("id"),
                "usage": data.get("usage", {}),
            },
        )
