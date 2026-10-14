"""Root-cause attribution and operational-constraint synthesis for failed reasoning."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .cognitive_memory import CognitiveMemoryEngine


class DeepReflexionEngine:
    """Attribute common failure classes, persist constraints, and expose them for later context."""
    def __init__(self, memory: CognitiveMemoryEngine | None = None):
        self.memory = memory or CognitiveMemoryEngine()

    @staticmethod
    def attribute(goal: str, failure: str, evidence: list[Any] | None = None) -> tuple[str, str]:
        text = f"{failure} {' '.join(str(getattr(item, 'content', '')) for item in (evidence or []))}".lower()
        if any(word in text for word in ("timeout", "timed out", "deadline")):
            return "timeout", "On timeout, retry at most once with a shorter query and bounded request timeout."
        if any(word in text for word in ("contradict", "conflict", "inconsistent")):
            return "contradictory_evidence", "When evidence conflicts, preserve both sources, compare publication dates and provenance, and do not synthesize a single certain claim."
        if any(word in text for word in ("parameter", "missing field", "required argument", "syntax", "invalid query")):
            return "query_or_parameter_error", "Validate required parameters and provider-specific query syntax before retrying; do not repeat an unchanged rejected request."
        if any(word in text for word in ("stale", "obsolete", "expired", "outdated")):
            return "temporal_obsolescence", "Check publication and effective dates; treat older evidence as superseded only when a newer source explicitly covers the same claim."
        return "insufficient_grounding", "If a reasoning path fails grounding checks, retrieve traceable evidence or state uncertainty instead of asserting unsupported conclusions."

    def reflect(self, *, goal: str, failure: str, domain: str = "general_reference",
                evidence: list[Any] | None = None, trace: dict | None = None) -> dict:
        cause, rule = self.attribute(goal, failure, evidence)
        key = hashlib.sha256(re.sub(r"\s+", " ", goal.strip().lower()).encode()).hexdigest()[:16]
        result = self.memory.synthesize_constraint(domain=domain, subject=f"failure-pattern:{key}",
                                                   predicate=cause, rule_text=rule, confidence=0.65)
        return {**result, "root_cause": cause, "rule_text": rule}

    def active_heuristics(self, domain: str | None = None, limit: int = 8) -> list[dict]:
        domains = [domain] if domain else None
        rules = []
        for item in self.memory.heuristics.get_rules(domain):
            if item.get("rule_type") not in {"reflexion_constraint", "tool_failure_constraint"}:
                continue
            rules.append({"domain": item["domain"], "predicate": item["predicate"],
                          "rule_text": item["rule_text"], "confidence": item["confidence"],
                          "provenance": "uie_integrity_ledger"})
        return rules[:max(1, min(int(limit), 20))]
