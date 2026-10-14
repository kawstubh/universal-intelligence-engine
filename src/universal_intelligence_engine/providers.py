"""Live provider adapters for UIE.

Providers are environment-configured and vendor-replaceable. No API secrets are
stored in source control.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse


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
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Provider request failed: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("Provider returned a non-object JSON response")
    return value


class FreePublicKnowledgeProvider:
    """Best-effort knowledge retrieval from free public reference APIs.

    General questions use Wikipedia's public API; medical and dental questions
    also query Europe PMC's public biomedical literature API. These sources do
    not require a search API key and are not a replacement for broad web search.
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

    def _search_wikipedia(self, request: IntelligenceRequest, query: str) -> list[Evidence]:
        language = (request.language or "en").strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}", language):
            language = "en"
        params = urlencode({
            "action": "query", "list": "search", "srsearch": query[:400],
            "srlimit": self.max_results, "format": "json", "utf8": 1,
        })
        url = f"https://{language}.wikipedia.org/w/api.php?{params}"
        try:
            data = _json_request(url, headers={"User-Agent": "UIE/0.5 (public knowledge retrieval)"})
        except RuntimeError:
            return []
        query_data = data.get("query", {})
        results = query_data.get("search", []) if isinstance(query_data, dict) else []
        evidence: list[Evidence] = []
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not item.get("title") or not isinstance(item.get("pageid"), int):
                continue
            snippet = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(item.get("snippet") or "")))).strip()
            evidence.append(Evidence(
                source="wikipedia-public-api",
                title=str(item["title"]),
                retrieved_at=_utc_now(),
                content=snippet,
                url=f"https://{language}.wikipedia.org/?curid={item['pageid']}",
                language=request.language or language,
                locale=request.locale,
                confidence=0.55,
                metadata={"provider": "wikipedia", "page_id": item["pageid"]},
            ))
        return evidence

    def _search_europe_pmc(self, request: IntelligenceRequest, query: str) -> list[Evidence]:
        params = urlencode({
            "query": query[:400], "format": "json", "pageSize": self.max_results,
            "resultType": "core",
        })
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
        try:
            data = _json_request(url, headers={"User-Agent": "UIE/0.5 (public biomedical research)"})
        except RuntimeError:
            return []
        result_list = data.get("resultList", {})
        results = result_list.get("result", []) if isinstance(result_list, dict) else []
        evidence: list[Evidence] = []
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            pmid, pmcid, doi = item.get("pmid"), item.get("pmcid"), item.get("doi")
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
                abstract = " ".join(str(part) for part in (item.get("journalTitle"), item.get("pubYear")) if part)
            evidence.append(Evidence(
                source="europe-pmc-public-api",
                title=str(item["title"]),
                retrieved_at=_utc_now(),
                content=abstract[:4000],
                url=result_url,
                language=request.language or "en",
                locale=request.locale,
                confidence=0.7,
                metadata={"provider": "europe-pmc", "publication_year": item.get("pubYear"), "journal": item.get("journalTitle"), "pmid": pmid, "doi": doi},
            ))
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
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6-luna")
        self.base_url = (base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )).rstrip("/")

    def reason(
        self,
        request: IntelligenceRequest,
        evidence: list[Evidence] | tuple[Evidence, ...],
    ) -> IntelligenceResponse:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

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
