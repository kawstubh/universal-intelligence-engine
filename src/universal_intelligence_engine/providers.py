"""Live provider adapters for UIE.

Providers are environment-configured and vendor-replaceable. No API secrets are
stored in source control.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from html import unescape
from typing import Any, ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .reasoning import NativeReasoningProvider

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Sanitized base class for provider failures."""


class ProviderTimeout(ProviderError):
    """Provider request timed out."""


class ProviderUnavailable(ProviderError):
    """Provider returned a 5xx response or could not be reached."""


class ProviderRateLimited(ProviderError):
    """Provider returned HTTP 429."""


class ProviderBadOutput(ProviderError):
    """Provider returned malformed JSON or an invalid response schema."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
    except httpx.TimeoutException as exc:
        raise ProviderTimeout("provider timeout") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            raise ProviderRateLimited("provider rate limited") from exc
        if 500 <= status <= 599:
            raise ProviderUnavailable("provider unavailable") from exc
        raise ProviderBadOutput("provider returned an unexpected HTTP status") from exc
    except httpx.RequestError as exc:
        raise ProviderUnavailable("provider request failed") from exc
    except HTTPError as exc:
        if exc.code == 429:
            raise ProviderRateLimited("provider rate limited") from exc
        if 500 <= exc.code <= 599:
            raise ProviderUnavailable("provider unavailable") from exc
        raise ProviderUnavailable("provider request failed") from exc
    except TimeoutError as exc:
        raise ProviderTimeout("provider timeout") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProviderTimeout("provider timeout") from exc
        raise ProviderUnavailable("provider unreachable") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderBadOutput("provider returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise ProviderBadOutput("provider returned a non-object JSON response")
    return value


class ProviderRateLimitError(RuntimeError):
    """Raised when a provider returns HTTP 429 after retry handling."""

    def __init__(self, provider: str, retry_after: float | None = None):
        self.provider = provider
        self.retry_after = retry_after
        self.code = "AI_RATE_LIMITED"
        super().__init__("Reasoning provider rate limit reached")


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


class FreePublicKnowledgeProvider:
    """Best-effort public-source retrieval with no paid search API or API key.

    General queries use Wikipedia's public MediaWiki API. Medical and dental
    queries also use Europe PMC, a public biomedical literature service. These
    are reference/research sources, not a substitute for broad web search.
    """

    _MEDICAL_TERMS = re.compile(
        r"\b(dental|dentist|tooth|teeth|oral|gum|gingiva|periodontal|caries|"
        r"cavity|endodont|orthodont|prosthodont|medicine|medical|clinical|"
        r"patient|disease|symptom|diagnosis|treatment|drug|medication|health|"
        r"anatomy|infection|surgery|therapy|pubmed|research study)\b",
        re.IGNORECASE,
    )

    def __init__(self, max_results: int = 8):
        self.max_results = max(1, min(max_results, 12))

    def search(self, request: IntelligenceRequest) -> list[Evidence]:
        query = request.goal.strip()
        if not query:
            return []

        evidence: list[Evidence] = []
        if self._MEDICAL_TERMS.search(query):
            evidence.extend(self._search_europe_pmc(request, query))
        evidence.extend(self._search_wikipedia(request, query))
        return evidence[: self.max_results]

    def _search_wikipedia(
        self, request: IntelligenceRequest, query: str
    ) -> list[Evidence]:
        language = (request.language or "en").strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}", language):
            language = "en"
        params = urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query[:400],
                "srlimit": self.max_results,
                "format": "json",
                "utf8": 1,
            }
        )
        url = f"https://{language}.wikipedia.org/w/api.php?{params}"
        try:
            data = _json_request(
                url,
                headers={"User-Agent": "UIE/0.5 (public knowledge retrieval)"},
            )
        except ProviderError as exc:
            logger.warning(
                "public knowledge source failed source=wikipedia error_class=%s",
                type(exc).__name__,
            )
            return []

        query_data = data.get("query", {})
        results = query_data.get("search", []) if isinstance(query_data, dict) else []
        evidence: list[Evidence] = []
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            page_id = item.get("pageid")
            if not isinstance(page_id, int):
                continue
            snippet = re.sub(
                r"\s+",
                " ",
                unescape(re.sub(r"<[^>]+>", " ", str(item.get("snippet") or ""))),
            ).strip()
            evidence.append(
                Evidence(
                    source="wikipedia-public-api",
                    title=str(item["title"]),
                    retrieved_at=_utc_now(),
                    content=snippet.strip(),
                    url=f"https://{language}.wikipedia.org/?curid={page_id}",
                    language=request.language or language,
                    locale=request.locale,
                    confidence=0.55,
                    metadata={"provider": "wikipedia", "page_id": page_id},
                )
            )
        return evidence

    def _search_europe_pmc(
        self, request: IntelligenceRequest, query: str
    ) -> list[Evidence]:
        params = urlencode(
            {
                "query": query[:400],
                "format": "json",
                "pageSize": self.max_results,
                "resultType": "core",
            }
        )
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
        try:
            data = _json_request(
                url,
                headers={"User-Agent": "UIE/0.5 (public biomedical research)"},
            )
        except ProviderError as exc:
            logger.warning(
                "public knowledge source failed source=europe-pmc error_class=%s",
                type(exc).__name__,
            )
            return []

        result_list = data.get("resultList", {})
        results = result_list.get("result", []) if isinstance(result_list, dict) else []
        evidence: list[Evidence] = []
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            pmid = item.get("pmid")
            pmcid = item.get("pmcid")
            doi = item.get("doi")
            if pmid:
                result_url = f"https://europepmc.org/article/MED/{pmid}"
            elif pmcid:
                normalized_pmcid = str(pmcid)
                if not normalized_pmcid.startswith("PMC"):
                    normalized_pmcid = f"PMC{normalized_pmcid}"
                result_url = f"https://europepmc.org/article/{normalized_pmcid}"
            elif doi:
                result_url = f"https://doi.org/{doi}"
            else:
                result_url = "https://europepmc.org/search?" + urlencode({"query": query})
            abstract = str(item.get("abstractText") or "").strip()
            if not abstract:
                journal = str(item.get("journalTitle") or "")
                year = str(item.get("pubYear") or "")
                abstract = " ".join(part for part in (journal, year) if part)
            evidence.append(
                Evidence(
                    source="europe-pmc-public-api",
                    title=str(item["title"]),
                    retrieved_at=_utc_now(),
                    content=abstract[:4000],
                    url=result_url,
                    language=request.language or "en",
                    locale=request.locale,
                    confidence=0.7,
                    metadata={
                        "provider": "europe-pmc",
                        "publication_year": item.get("pubYear"),
                        "journal": item.get("journalTitle"),
                        "pmid": pmid,
                        "doi": doi,
                    },
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

    DEFAULT_BASE_URLS: ClassVar[dict[str, str]] = {
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
        self.provider = (
            (provider or os.getenv("REASONING_PROVIDER") or "openai").strip().lower()
        )
        self.model = (
            model or os.getenv("REASONING_MODEL") or os.getenv("OPENAI_MODEL") or ""
        ).strip() or None
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
        self.api_key = (
            api_key
            or provider_keys.get(self.provider)
            or os.getenv("REASONING_API_KEY")
        )

    def _messages(
        self, request: IntelligenceRequest, evidence: Sequence[Evidence]
    ) -> list[dict[str, str]]:
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
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _reason_once(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
    ) -> IntelligenceResponse:
        if self.provider not in self.DEFAULT_BASE_URLS:
            raise ProviderNotConfiguredError(
                "reasoning", f"Unsupported reasoning provider: {self.provider}"
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
                payload={"model": self.model, "input": messages},
            )
            answer = data.get("output_text")
            if not answer:
                parts = []
                for item in data.get("output", []):
                    for content in (
                        item.get("content", []) if isinstance(item, dict) else []
                    ):
                        if (
                            isinstance(content, dict)
                            and content.get("type") == "output_text"
                        ):
                            parts.append(str(content.get("text", "")))
                answer = "\n".join(p for p in parts if p).strip()
        else:
            data = _json_request(
                f"{self.base_url}/chat/completions",
                method="POST",
                headers={"Authorization": f"Bearer {self.api_key}"},
                payload={"model": self.model, "messages": messages},
            )
            choices = data.get("choices") or []
            answer = None
            if choices and isinstance(choices[0], dict):
                answer = (choices[0].get("message") or {}).get("content")
        if not answer:
            raise ProviderBadOutput("provider returned no output text")
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

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
    ) -> IntelligenceResponse:
        try:
            primary_error: ProviderError | None = None
            for attempt in range(3):
                try:
                    return self._reason_once(request, evidence)
                except ProviderRateLimited:
                    primary_error = ProviderRateLimited("provider rate limited")
                    if attempt < 2:
                        time.sleep(min(2**attempt, 8.0))
                except ProviderError as exc:
                    primary_error = exc
                    break
            return self._fallback_after_primary_failure(
                request,
                evidence,
                primary_error or ProviderUnavailable("primary provider failed"),
            )
        except ProviderNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Deliberately log only the exception type; never log exception text or payload.
            logger.warning(
                "provider guard caught exception class=%s", type(exc).__name__
            )
            try:
                native = NativeReasoningProvider().reason(request, evidence)
                metadata = dict(native.metadata)
                metadata["provider_used"] = "native-fallback"
                metadata["provider_error"] = "AI unavailable"
                return IntelligenceResponse(
                    answer=native.answer or "AI unavailable",
                    evidence=native.evidence,
                    confidence=native.confidence,
                    actions=native.actions,
                    metadata=metadata,
                )
            except Exception:  # noqa: BLE001
                logger.warning("native provider failed; returning static fallback")
                return IntelligenceResponse(
                    answer="AI unavailable",
                    evidence=[],
                    confidence=0,
                    metadata={
                        "provider_used": "static-fallback",
                        "provider_error": "AI unavailable",
                    },
                )

    def _fallback_after_primary_failure(
        self,
        request: IntelligenceRequest,
        evidence: Sequence[Evidence],
        primary_error: Exception,
    ) -> IntelligenceResponse:
        fallback = os.getenv("REASONING_FALLBACK_PROVIDER", "").strip().lower()
        if not fallback or fallback == self.provider:
            native = NativeReasoningProvider().reason(request, evidence)
            metadata = dict(native.metadata)
            metadata["provider_used"] = "native-fallback"
            metadata["provider_error"] = "AI unavailable"
            return IntelligenceResponse(
                answer=native.answer,
                evidence=native.evidence,
                confidence=native.confidence,
                actions=native.actions,
                metadata=metadata,
            )
        fallback_model = os.getenv("REASONING_FALLBACK_MODEL", "").strip() or None
        fallback_base = os.getenv("REASONING_FALLBACK_BASE_URL", "").strip() or None
        fallback_key = {
            "openai": os.getenv("OPENAI_API_KEY"),
            "gemini": os.getenv("GEMINI_API_KEY"),
            "groq": os.getenv("GROQ_API_KEY"),
        }.get(fallback) or os.getenv("REASONING_API_KEY")
        if not fallback_key:
            native = NativeReasoningProvider().reason(request, evidence)
            metadata = dict(native.metadata)
            metadata["provider_used"] = "native-fallback"
            return IntelligenceResponse(
                answer=native.answer,
                evidence=native.evidence,
                confidence=native.confidence,
                actions=native.actions,
                metadata=metadata,
            )
        secondary = OpenAIResponsesReasoningProvider(
            api_key=fallback_key,
            model=fallback_model,
            base_url=fallback_base,
            provider=fallback,
        )
        try:
            return secondary._reason_once(request, evidence)
        except ProviderError:
            native = NativeReasoningProvider().reason(request, evidence)
            metadata = dict(native.metadata)
            metadata["provider_used"] = "native-fallback"
            return IntelligenceResponse(
                answer=native.answer,
                evidence=native.evidence,
                confidence=native.confidence,
                actions=native.actions,
                metadata=metadata,
            )


class NoKnowledgeProvider:
    """Explicit offline fallback that returns no fabricated evidence."""

    def search(self, request: IntelligenceRequest) -> list[Evidence]:
        return []
