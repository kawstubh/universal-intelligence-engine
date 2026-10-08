
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


class DisabledKnowledgeProvider:
    """No-op knowledge provider used when web search is intentionally disabled."""

    def search(self, request: IntelligenceRequest) -> list[Evidence]:
        return []


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
    """Vendor-neutral reasoning provider.

    Supported providers:
      - openai: OpenAI Responses API
      - gemini: Gemini OpenAI-compatible Chat Completions API
      - groq: Groq OpenAI-compatible Chat Completions API

    Configuration:
      REASONING_PROVIDER=openai|gemini|groq
      REASONING_BASE_URL=<optional override>
      REASONING_MODEL=<provider model id>
      REASONING_API_KEY=<generic key, optional>
      GEMINI_API_KEY=<Gemini key, preferred for provider=gemini>
      GROQ_API_KEY=<Groq key, preferred for provider=groq>
      OPENAI_API_KEY=<OpenAI key, preferred for provider=openai>
    """

    DEFAULT_BASE_URLS = {
        "openai": "https://api.openai.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "groq": "https://api.groq.com/openai/v1",
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider: str | None = None,
    ):
        self.provider = (provider or os.getenv("REASONING_PROVIDER", "openai")).strip().lower()
        self.model = model or os.getenv("REASONING_MODEL") or os.getenv("OPENAI_MODEL")
        self.base_url = (
            base_url
            or os.getenv("REASONING_BASE_URL")
            or self.DEFAULT_BASE_URLS.get(self.provider, "")
        ).rstrip("/")
        provider_keys = {
            "openai": os.getenv("OPENAI_API_KEY"),
            "gemini": os.getenv("GEMINI_API_KEY"),
            "groq": os.getenv("GROQ_API_KEY"),
        }
        self.api_key = api_key or provider_keys.get(self.provider) or os.getenv("REASONING_API_KEY")

    def _messages(self, request: IntelligenceRequest, evidence: list[Evidence] | tuple[Evidence, ...]) -> list[dict[str, str]]:
        evidence_block = "\n\n".join(
            f"[{idx}] {item.title}\nSource: {item.source}\nURL: {item.url or 'n/a'}\n{item.content}"
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
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: list[Evidence] | tuple[Evidence, ...],
    ) -> IntelligenceResponse:
        if self.provider not in self.DEFAULT_BASE_URLS:
            raise ProviderNotConfiguredError(
                "reasoning",
                f"Unsupported reasoning provider: {self.provider}",
            )
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "reasoning",
                f"Reasoning provider is not configured: API key is missing for {self.provider}",
            )
        if not self.model:
            raise ProviderNotConfiguredError(
                "reasoning",
                "Reasoning provider is not configured: REASONING_MODEL is missing",
            )

        messages = self._messages(request, evidence)
        if self.provider == "openai":
            data = _json_request(
                f"{self.base_url}/responses",
                method="POST",
                headers={"Authorization": f"Bearer {self.api_key}"},
                payload={
                    "model": self.model,
                    "input": messages,
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
        else:
            data = _json_request(
                f"{self.base_url}/chat/completions",
                method="POST",
                headers={"Authorization": f"Bearer {self.api_key}"},
                payload={
                    "model": self.model,
                    "messages": messages,
                },
            )
            choices = data.get("choices") or []
            answer = None
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                answer = message.get("content")

        if not answer:
            raise RuntimeError("Reasoning provider returned no output text")

        return IntelligenceResponse(
            answer=str(answer),
            evidence=evidence,
            confidence=None,
            metadata={
                "provider": self.provider,
                "model": self.model,
                "response_id": data.get("id"),
                "usage": data.get("usage", {}),
            },
        )
\n