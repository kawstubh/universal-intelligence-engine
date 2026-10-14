"""Bounded, evidence-grounded reflexion over graph conflicts and stale claims."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .graph_store import KnowledgeGraphStore
from .heuristic_memory import HeuristicMemory


def _temporal_year(anchor: Any) -> int | None:
    if not anchor:
        return None
    match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", str(anchor))
    return int(match.group(1)) if match else None


class ReflexiveGraphValidator:
    """Turn graph uncertainty into cautious persistent constraints and bounded discovery."""

    def __init__(self, db_path: str, heuristic_memory: HeuristicMemory | None = None,
                 learner=None, stale_after_years: int = 2, low_confidence_threshold: float = 0.45):
        self.db_path = str(db_path)
        self.heuristics = heuristic_memory or HeuristicMemory(self.db_path)
        self.learner = learner
        self.stale_after_years = max(1, int(stale_after_years))
        self.low_confidence_threshold = min(0.75, max(0.0, float(low_confidence_threshold)))

    def inspect(self, query: str, graph: dict, claims: list[dict] | None = None) -> dict:
        claims_by_id = {str(c.get("id")): c for c in (claims or []) if c.get("id") is not None}
        issues = []
        for edge in graph.get("relations", []):
            claim = claims_by_id.get(str(edge.get("claim_id")), {})
            status = edge.get("status", "unverified_claim")
            year = _temporal_year(claim.get("temporal_anchor"))
            stale = year is not None and year <= datetime.now(timezone.utc).year - self.stale_after_years
            low_confidence = float(edge.get("confidence", 0.0)) < self.low_confidence_threshold
            if status == "conflicted_evidence":
                issue_type = "conflicted_edge"
                rule = (f"Entity {edge.get('source_name')} has conflicting reported relations for "
                        f"'{edge.get('predicate')}'; do not assert '{edge.get('target_name')}' unconditionally. "
                        "Preserve both claims, disclose the conflict, and seek independent corroboration.")
            elif stale:
                issue_type = "stale_temporal_anchor"
                rule = (f"Do not assert the claim about {edge.get('source_name')} / {edge.get('predicate')} "
                        f"as current based only on its {year} temporal anchor; seek newer dated evidence.")
            elif low_confidence:
                issue_type = "low_confidence_edge"
                rule = (f"Treat the relation {edge.get('source_name')} --{edge.get('predicate')}--> "
                        f"{edge.get('target_name')} as uncertain; require independent corroboration before asserting it.")
            else:
                continue
            issues.append({"type": issue_type, "relation_id": edge.get("id"),
                           "subject": str(edge.get("source_name") or query),
                           "predicate": str(edge.get("predicate") or "related_to"),
                           "target": str(edge.get("target_name") or ""),
                           "domain": str(edge.get("domain") or "general_reference"),
                           "provenance_url": edge.get("provenance_url"), "rule_text": rule})

        stored = []
        for issue in issues:
            result = self.heuristics.store_constraint(
                domain=issue["domain"], subject=issue["subject"], predicate=issue["predicate"],
                rule_text=issue["rule_text"], provenance_urls=[issue["provenance_url"]] if issue["provenance_url"] else [],
                confidence=0.65, rule_type=issue["type"],
            )
            stored.append({**issue, **result})

        discovery = {"scheduled": False, "status": "not_needed"}
        if issues:
            if self.learner is None:
                from .autonomous_learner import AutonomousLearner
                from .contradiction import ContradictionEngine
                self.learner = AutonomousLearner(claim_memory=ContradictionEngine(self.db_path))
            # One concise targeted query keeps exploration bounded; the learner itself caps domains, URLs and runtime.
            targets = "; ".join(f"{i['subject']} {i['predicate']} {i['target']} ({i['type']})" for i in issues[:3])
            discovery = self.learner.trigger_background(f"Resolve these evidence gaps with independent, dated sources: {targets}. Original query: {query[:180]}")
        rules = self.heuristics.get_rules()
        query_lower = query.lower()
        recalled = [r for r in rules if r.get("rule_type") in {"conflicted_edge", "stale_temporal_anchor", "low_confidence_edge"}
                    and (str(r.get("subject", "")).lower() in query_lower or query_lower in str(r.get("subject", "")).lower())]
        return {"issues": issues, "constraints_stored": stored, "recalled_constraints": recalled,
                "discovery": discovery, "automatic_assertion_allowed": not bool(issues),
                "note": "Constraints are cautionary heuristics, not factual resolution; automated web evidence remains unverified."}
