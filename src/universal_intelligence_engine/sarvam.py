"""Sarvam Token Factory reasoning adapter for UIE.

The provider is selected by UIE_REASONING_PROVIDER=sarvam. Credentials are
read only from SARVAM_API_KEY and are never returned by health or logs.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence

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
        # Sarvam-105B reasoning tokens count against max_tokens. 512 can be consumed
        # before any user-visible answer is generated. 4096 is the documented
        # Starter-tier completion budget.
        configured_tokens = max_tokens or int(os.getenv("SARVAM_MAX_TOKENS", "4096"))
        self.max_tokens = max(256, min(configured_tokens, 4096))

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
            f"Context: {json.dumps(dict(request.context), ensure_ascii=False)}\n"
            f"Constraints: {json.dumps(dict(request.constraints), ensure_ascii=False)}\n\n"
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

        # Sarvam documents that reasoning tokens count against max_tokens. If
        # reasoning consumed the first budget and finish_reason is "length",
        # retry once with thinking disabled to reserve tokens for the answer.
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if (
            (not isinstance(answer, str) or not answer.strip())
            and isinstance(choice, dict)
            and choice.get("finish_reason") == "length"
        ):
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
                    "reasoning_effort": None,
                },
                timeout=float(os.getenv("SARVAM_TIMEOUT_SECONDS", "90")),
            )
            choices = data.get("choices")
            message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
            answer = message.get("content") if isinstance(message, dict) else None
            if isinstance(answer, list):
                answer = "\n".join(
                    str(part.get("text", "")) for part in answer if isinstance(part, dict)
                ).strip()

        if not isinstance(answer, str) or not answer.strip():
            choice = choices[0] if isinstance(choices, list) and choices else {}
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            diagnostics = {
                "response_keys": sorted(data.keys()),
                "choices_type": type(choices).__name__,
                "choices_count": len(choices) if isinstance(choices, list) else None,
                "choice_keys": sorted(choice.keys()) if isinstance(choice, dict) else [],
                "message_keys": sorted(message.keys()) if isinstance(message, dict) else [],
                "content_type": type(message.get("content")).__name__ if isinstance(message, dict) else None,
                "content_is_empty": not bool(message.get("content")) if isinstance(message, dict) else None,
                "reasoning_content_present": bool(message.get("reasoning_content")) if isinstance(message, dict) else False,
                "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
                "provider_error_present": bool(data.get("error")),
            }
            raise RuntimeError(
                "Sarvam returned no completion text; safe response diagnostics: "
                + json.dumps(diagnostics, sort_keys=True)
            )

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
