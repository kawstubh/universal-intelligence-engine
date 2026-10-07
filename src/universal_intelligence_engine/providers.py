
"""Live provider adapters for UIE.

Providers are environment-configured and vendor-replaceable. No API secrets are
stored in source control.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from typing import Any

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Provider request failed: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Provider returned a non-object JSON response")
    return value


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a live provider cannot run because its credentials/config are absent."""
    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.code = "PROVIDER_NOT_CONFIGURED"
        super().__init__(message)


class BraveSearchProvider:
    """Live web search through Brave Search API."""

    def __init__(self, api_key: str | None = None, max_results: int = 8):
        self.api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        self.max_results = max(1, min(max_results, 20))

    def search(self, request: IntelligenceRequest) -> list[Evidence]:
        if not self.api_key:
            raise ProviderNotConfiguredError("knowledge", "Knowledge provider is not configured: BRAVE_SEARCH_API_KEY is missing")
        query = request.goal.strip()
        if request.locale:
            query = f"{query} region:{request.locale}"
        url = (
            "https://api.search.brave.com/res/v1/web/search"
            f"?q={quote_plus(query)}&count={self.max_results}"
        )
        data = _json_request(
            url,
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
        )
        results = data.get("web", {}).get("results", [])
        evidence: list[Evidence] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            evidence.append(
                Evidence(
                    source="brave-web",
                    title=str(item.get("title") or "Untitled result"),
                    retrieved_at=_utc_now(),
                    content=str(item.get("description") or ""),
                    url=item.get("url"),
                    language=request.language,
                    locale=request.locale,
                    confidence=0.65,
                    metadata={"provider": "brave"},
                )
            )
        return evidence


class OpenAIResponsesReasoningProvider:
    """Reason over retrieved evidence through OpenAI's Responses API.

    Uses OPENAI_API_KEY and OPENAI_MODEL. The Responses API is the current API
    surface for new OpenAI integrations.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL")
        self.base_url = (base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )).rstrip("/")

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: list[Evidence] | tuple[Evidence, ...],
    ) -> IntelligenceResponse:
        if not self.api_key:
            raise ProviderNotConfiguredError("reasoning", "Reasoning provider is not configured: OPENAI_API_KEY is missing")
        if not self.model:
            raise ProviderNotConfiguredError("reasoning", "Reasoning provider is not configured: OPENAI_MODEL is missing")

        evidence_block = "\n\n".join(
            f"[{idx}] {item.title}\nSource: {item.source}\nURL: {item.url or 'n/a'}\n"
            f"{item.content}"
            for idx, item in enumerate(evidence, start=1)
        )
        system = (
            "You are the reasoning layer of a domain-agnostic intelligence engine. "
            "Use the supplied evidence, distinguish facts from uncertainty, and do "
            "not invent citations. If evidence is insufficient, say so. Respect all "
            "request constraints and return a concise useful answer."
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
            f"{self.base_url}/responses",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        answer = data.get("output_text")
        if not answer:
            parts: list[str] = []
            for item in data.get("output", []):
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        parts.append(str(content.get("text", "")))
            answer = "\n".join(p for p in parts if p).strip()
        if not answer:
            raise RuntimeError("Reasoning provider returned no output text")

        return IntelligenceResponse(
            answer=answer,
            evidence=evidence,
            confidence=None,
            metadata={
                "provider": "openai-responses",
                "model": self.model,
                "response_id": data.get("id"),
                "usage": data.get("usage", {}),
            },
        )

