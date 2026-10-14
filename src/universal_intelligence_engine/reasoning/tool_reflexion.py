from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any



class ToolReflexionEngine:
    """Convert external-tool failures into bounded, auditable operational constraints."""

    SUPPORTED_DOMAINS = {"europe-pmc", "wikipedia", "crawler"}

    def __init__(self, memory=None):
        if memory is None:
            from ..learning.heuristic_memory import HeuristicMemory
            memory = HeuristicMemory()
        self.memory = memory

    @staticmethod
    def _query_key(query: str) -> str:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def reflect_failure(self, domain: str, query: str, failure_type: str, detail: str = "") -> dict[str, Any]:
        """Deterministically critique a failure; never promote a guess as a verified fact."""
        normalized_domain = domain.strip().lower()
        if normalized_domain not in self.SUPPORTED_DOMAINS:
            return {"stored": False, "reason": "unsupported_domain"}
        safe_query = re.sub(r"\s+", " ", str(query))[:500]
        normalized_detail = str(detail).lower()[:500]
        subject = f"query:{self._query_key(safe_query)}"
        predicate = "reformulate_after_failure"
        rule = (
            f"For this {normalized_domain} query pattern, validate syntax and parameters, "
            "then reformulate once before treating an empty or failed response as evidence."
        )
        if normalized_domain == "europe-pmc" and re.search(r"\b(and|or|not)\b", safe_query):
            predicate = "uppercase_boolean_operators"
            rule = "Use uppercase AND, OR, and NOT in Europe PMC query expressions; validate syntax before retrying."
        elif "timeout" in failure_type.lower() or "timeout" in normalized_detail or "timed out" in normalized_detail:
            predicate = "shorten_query_after_timeout"
            rule = f"If this {normalized_domain} query times out, retry once with a shorter query and a bounded timeout."
        elif normalized_domain == "crawler":
            predicate = "validate_url_and_crawl_policy"
            rule = "For crawler failures, validate the HTTPS URL and approved domain, then check robots.txt and response type before retrying."
        elif "json" in normalized_detail or "decode" in normalized_detail:
            predicate = "validate_response_format"
            rule = f"For {normalized_domain} calls, validate the response content type and JSON structure before parsing."
        result = self.memory.store_constraint(
            domain=normalized_domain,
            subject=subject,
            predicate=predicate,
            rule_text=rule,
            confidence=0.65,
            rule_type="tool_failure_constraint",
        )
        return {**result, "domain": normalized_domain, "predicate": predicate, "rule_text": rule,
                "failure_type": failure_type}

    def execute(self, domain: str, query: str, operation: Callable[[], Any], *, is_empty: Callable[[Any], bool] | None = None) -> Any:
        """Run an operation, reflexively recording exceptions and explicitly empty results."""
        try:
            result = operation()
        except Exception as exc:
            self.reflect_failure(domain, query, type(exc).__name__, str(exc))
            raise
        if is_empty is not None:
            try:
                empty = bool(is_empty(result))
            except Exception:
                empty = False
            if empty:
                self.reflect_failure(domain, query, "empty_results", "Operation returned an empty result payload")
        return result

    def prepare_query(self, domain: str, query: str) -> str:
        """Apply learned safe rewrites for the same normalized query pattern."""
        key = f"query:{self._query_key(query)}"
        rules = self.memory.get_rules(domain.strip().lower())
        predicates = {item.get("predicate") for item in rules
                      if item.get("subject") == key and item.get("rule_type") == "tool_failure_constraint"}
        prepared = query
        if "uppercase_boolean_operators" in predicates:
            prepared = re.sub(r"\b(and|or|not)\b", lambda match: match.group(1).upper(), prepared, flags=re.IGNORECASE)
        if "shorten_query_after_timeout" in predicates and len(prepared) > 250:
            prepared = prepared[:250]
        return prepared

    def applicable_rules(self, domain: str | None = None, query: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Return concise procedural constraints for reasoning-context injection."""
        normalized_domain = domain.strip().lower() if isinstance(domain, str) else ""
        domains = [normalized_domain] if normalized_domain in self.SUPPORTED_DOMAINS else sorted(self.SUPPORTED_DOMAINS)
        rules: list[dict[str, Any]] = []
        query_key = f"query:{self._query_key(query)}" if query else None
        for name in domains:
            for item in self.memory.get_rules(name):
                if item.get("rule_type") != "tool_failure_constraint":
                    continue
                if query_key and item.get("subject") != query_key:
                    continue
                rules.append({"domain": name, "rule_text": item.get("rule_text", ""),
                              "predicate": item.get("predicate", ""), "confidence": item.get("confidence", 0.0),
                              "provenance": "uie_integrity_ledger/heuristic_rules"})
        return rules[:max(1, min(int(limit), 20))]
