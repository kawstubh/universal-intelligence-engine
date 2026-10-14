"""Bounded directed causal graph projections; not a clinical or operational safety oracle."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

CAUSAL_PREDICATES = frozenset({"CAUSES", "INHIBITS", "TRIGGERS", "CONTRAINDICATES", "PREVENTS"})
_RISK_PREDICATES = {"CONTRAINDICATES", "INHIBITS"}
_BENEFIT_PREDICATES = {"PREVENTS"}


@dataclass(frozen=True)
class CausalSimulationResult:
    target_entity: str
    intervention_action: str
    max_depth: int
    projected_benefits: list[dict[str, Any]] = field(default_factory=list)
    projected_risks: list[dict[str, Any]] = field(default_factory=list)
    critical_contraindications: list[dict[str, Any]] = field(default_factory=list)
    safety_score: float = 0.5
    traversed_edges: int = 0
    limitations: tuple[str, ...] = (
        "Projections are graph-derived hypotheses, not validated causal effects.",
        "No clinical, weapon, or other high-stakes action is authorized by this result.",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_entity": self.target_entity, "intervention_action": self.intervention_action,
            "max_depth": self.max_depth, "projected_benefits": self.projected_benefits,
            "projected_risks": self.projected_risks,
            "critical_contraindications": self.critical_contraindications,
            "safety_score": self.safety_score, "traversed_edges": self.traversed_edges,
            "limitations": list(self.limitations),
        }


class CausalWorldModel:
    """Traverse directed, explicitly typed edges and report bounded possible consequences."""

    def __init__(self, graph_store: Any):
        self.graph_store = graph_store

    def simulate_intervention(self, target_entity: str, intervention_action: str,
                              max_depth: int = 2) -> CausalSimulationResult:
        if not target_entity or not target_entity.strip():
            raise ValueError("target_entity must not be empty")
        if not intervention_action or not intervention_action.strip():
            raise ValueError("intervention_action must not be empty")
        depth_limit = max(1, min(int(max_depth), 3))
        graph = self.graph_store.get_entity_subgraph(target_entity, max_hops=depth_limit)
        root = graph.get("root")
        if not root:
            return CausalSimulationResult(target_entity, intervention_action, depth_limit,
                                          projected_risks=[{"entity": target_entity, "reason": "target_not_in_graph"}],
                                          safety_score=0.0)
        root_id = root["id"]
        outgoing: dict[str, list[dict[str, Any]]] = {}
        for edge in graph.get("relations", []):
            predicate = str(edge.get("predicate", "")).strip().upper().replace("-", "_").replace(" ", "_")
            if predicate in CAUSAL_PREDICATES and edge.get("status") not in {"rejected", "superseded_temporal"}:
                normalized = dict(edge)
                normalized["causal_predicate"] = predicate
                outgoing.setdefault(str(edge["source_entity_id"]), []).append(normalized)
        benefits: list[dict[str, Any]] = []
        risks: list[dict[str, Any]] = []
        critical: list[dict[str, Any]] = []
        seen_paths: set[tuple[str, ...]] = set()
        queue = deque([(root_id, 0, [], False)])
        traversed = 0
        action = intervention_action.strip().lower()
        action_inhibits = any(word in action for word in ("inhibit", "block", "suppress", "disable", "prevent"))
        while queue:
            node_id, depth, path, upstream_inhibition = queue.popleft()
            if depth >= depth_limit:
                continue
            for edge in outgoing.get(node_id, []):
                next_id = str(edge["target_entity_id"])
                next_path = path + [edge]
                path_ids = tuple(str(item["id"]) for item in next_path)
                if path_ids in seen_paths:
                    continue
                seen_paths.add(path_ids)
                traversed += 1
                predicate = edge["causal_predicate"]
                confidence = max(0.0, min(1.0, float(edge.get("confidence", 0.0))))
                name = str(edge.get("target_name", next_id))
                source_name = str(edge.get("source_name", node_id))
                projection = {"entity": name, "via": [str(e.get("source_name", "")) + " -" + e["causal_predicate"] + "-> " + str(e.get("target_name", "")) for e in next_path],
                              "confidence": round(confidence, 4), "depth": depth + 1,
                              "interpretation": "graph-derived hypothesis; requires independent validation"}
                if predicate == "CONTRAINDICATES":
                    critical.append({**projection, "reason": f"{source_name} contraindicates {name}"})
                elif predicate == "PREVENTS":
                    benefits.append({**projection, "reason": f"{source_name} is recorded as preventing {name}"})
                elif predicate in {"CAUSES", "TRIGGERS"}:
                    if upstream_inhibition or action_inhibits:
                        critical.append({**projection, "reason": "intervention/inhibition intersects a downstream causal trigger; direction and mechanism need validation"})
                    else:
                        risks.append({**projection, "reason": f"{source_name} may {predicate.lower()} {name}"})
                elif predicate == "INHIBITS":
                    risks.append({**projection, "reason": f"{source_name} is recorded as inhibiting {name}; downstream dependencies may be affected"})
                queue.append((next_id, depth + 1, next_path, upstream_inhibition or predicate == "INHIBITS" or action_inhibits))
        risk_load = min(1.0, 0.25 * len(risks) + 0.45 * len(critical))
        benefit_credit = min(0.15, 0.05 * len(benefits))
        safety = max(0.0, min(1.0, 0.75 - risk_load + benefit_credit))
        if critical:
            safety = min(safety, 0.2)
        return CausalSimulationResult(target_entity, intervention_action, depth_limit, benefits, risks, critical,
                                      round(safety, 3), traversed)


def simulate_intervention(target_entity: str, intervention_action: str, max_depth: int = 2,
                          *, graph_store: Any | None = None) -> CausalSimulationResult:
    """Convenience API; pass a KnowledgeGraphStore explicitly to avoid hidden global state."""
    if graph_store is None:
        raise ValueError("graph_store is required; no implicit persistent graph is opened")
    return CausalWorldModel(graph_store).simulate_intervention(target_entity, intervention_action, max_depth)
