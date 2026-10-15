"""Reasoning-facing claim retrieval and optional non-blocking gap-triggered learning."""
from __future__ import annotations

import os
import json
import re
import warnings as deprecation_warnings
from pathlib import Path
from typing import Any

from .contradiction import ContradictionEngine
from .semantic_matcher import SemanticMatcher
from .graph_store import KnowledgeGraphStore
from .graph_reflexion import ReflexiveGraphValidator
from ..db.storage_engine import StorageEngine
from .contradiction import _temporal_rank


def _valid_time(value: Any) -> tuple[int, int, int]:
    rank = _temporal_rank(str(value) if value else None)
    return rank if rank is not None else (0, 0, 0)

def _claim_warning(status: str | None) -> str | None:
    return {"conflicted_evidence": "Conflicted evidence: do not use as an unqualified fact; compare both source records.",
            "unverified_claim": "Unverified extracted claim: validate against source evidence before relying on it.",
            "superseded_temporal": "Older time-scoped evidence marked superseded by a newer dated record."}.get(status)

def _is_stale(anchor: Any) -> bool:
    rank = _temporal_rank(str(anchor) if anchor else None)
    from datetime import datetime, timezone
    return bool(rank and rank[0] <= datetime.now(timezone.utc).year - 2)


class ClaimQueryService:
    """Read structured claim memory without upgrading unverified claims to facts."""

    def __init__(self, memory: ContradictionEngine | None = None, db_path: str | Path | None = None, learner=None):
        self.memory = memory or ContradictionEngine(str(db_path or os.getenv("UIE_CLAIM_MEMORY_PATH", ".data/claim_memory.sqlite3")))
        self.learner = learner

    async def aquery_graph(self, subject: str, *, max_hops: int = 2, storage: StorageEngine | None = None) -> dict[str, Any]:
        """Async graph query path; entity/relation reads are performed by StorageEngine."""
        # Bypass KnowledgeGraphStore.__init__: it performs synchronous SQLite DDL.
        # Async graph reads must be served only by the supplied StorageEngine.
        store = object.__new__(KnowledgeGraphStore)
        store.db_path = str(self.memory.db_path)
        store.confidence_decay = 0.8
        graph=await store.aget_entity_subgraph(subject,max_hops=max_hops,storage=storage)
        warnings=[{"relation_id":edge.get("id"),"status":edge.get("status"),"warning":"Graph edge is evidence, not a verified fact.","provenance_url":edge.get("provenance_url")} for edge in graph.get("relations",[]) if edge.get("status") in {"conflicted_evidence","unverified_claim","superseded_temporal"}]
        return {"subject_query":subject,"knowledge_graph":graph,"graph_warnings":warnings,"usage_warning":"Extracted claims are evidence candidates, not verified facts. Check provenance and temporal scope before reasoning from them."}

    async def aquery(self, subject: str | None = None, limit: int = 20, *,
                     include_graph: bool = True, max_hops: int = 2,
                     storage: StorageEngine | None = None, min_confidence: float = 0.0,
                     statuses: set[str] | None = None, domain: str | None = None,
                     trigger_learning: bool = False) -> dict[str, Any]:
        """Feature-complete async query path over the shared SQLite/PostgreSQL adapter.

        Claim filtering, provenance, contradiction records and temporal ordering are read
        asynchronously. Semantic similarity is CPU-only and therefore remains synchronous.
        """
        limit = max(1, min(int(limit), 100))
        min_confidence = max(0.0, min(1.0, float(min_confidence)))
        own_storage = storage is None
        adapter = storage or StorageEngine(sqlite_path=self.memory.db_path)
        if own_storage:
            await adapter.connect()
        try:
            await adapter.initialize_schema()
            if subject and domain and domain.strip().lower() in {"europe-pmc", "wikipedia", "crawler"}:
                from ..reasoning.tool_reflexion import ToolReflexionEngine
                reflexion = ToolReflexionEngine()
                subject = await reflexion.aprepare_query(domain, subject, storage=adapter)
            rows = await adapter.fetchall("SELECT * FROM claims")
            all_claims = []
            for row in rows:
                item = dict(row)
                try:
                    item["provenance_urls"] = json.loads(item.get("provenance_urls") or "[]")
                except (TypeError, json.JSONDecodeError):
                    item["provenance_urls"] = []
                item["warning"] = _claim_warning(item.get("status"))
                all_claims.append(item)
            if subject:
                exact = [c for c in all_claims if subject.lower() in str(c.get("subject", "")).lower()]
                if exact:
                    claims = exact
                else:
                    claims = SemanticMatcher(threshold=0.18).match(subject, all_claims, threshold=0.18, limit=limit)
            else:
                claims = all_claims
            claims = [c for c in claims if float(c.get("confidence") or 0.0) >= min_confidence]
            if statuses is not None:
                claims = [c for c in claims if c.get("status") in statuses]
            if domain:
                claims = [c for c in claims if str(c.get("domain", "")).lower() == domain.strip().lower()]
            claims.sort(key=lambda c: (_valid_time(c.get("temporal_anchor")), str(c.get("created_at") or ""), str(c.get("id") or "")))
            claims = claims[-limit:]
            projected = [{key: claim.get(key) for key in (
                "id", "subject", "predicate", "object_claim", "temporal_anchor", "confidence",
                "provenance_url", "provenance_urls", "domain", "status", "warning", "created_at", "updated_at"
            )} for claim in claims]
            ids = [c["id"] for c in claims if c.get("id")]
            provenance_ledger = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                ledger_rows = await adapter.fetchall(
                    f"SELECT record_type,record_id,payload_json,prev_hash,record_hash,sequence,created_at FROM memory_chain WHERE record_type='claim' AND record_id IN ({placeholders}) ORDER BY sequence",
                    tuple(ids))
                provenance_ledger = [dict(row) for row in ledger_rows]
            contradiction_rows = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                contradiction_rows = [dict(row) for row in await adapter.fetchall(
                    f"SELECT * FROM contradiction_log WHERE existing_claim_id IN ({placeholders}) OR incoming_claim_id IN ({placeholders}) ORDER BY created_at",
                    tuple(ids + ids))]
            warnings = [{"claim_id": c["id"], "status": c["status"], "warning": c["warning"]}
                        for c in claims if c.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}]
            response: dict[str, Any] = {
                "subject_query": subject, "count": len(projected), "claims": projected,
                "warnings": warnings, "contradictions": contradiction_rows,
                "provenance_ledger": provenance_ledger,
                "usage_warning": "Extracted claims are evidence candidates, not verified facts. Check provenance and temporal scope before reasoning from them.",
            }
            if include_graph and subject:
                graph_result = await self.aquery_graph(subject, max_hops=max_hops, storage=adapter)
                response["knowledge_graph"] = graph_result.get("knowledge_graph", {})
                response["graph_warnings"] = graph_result.get("graph_warnings", [])
                response["warnings"].extend(graph_result.get("graph_warnings", []))
                relation_ids = [str(edge.get("id")) for edge in response["knowledge_graph"].get("relations", []) if edge.get("id")]
                response["graph_contradictions"] = []
                if relation_ids:
                    relation_placeholders = ",".join("?" for _ in relation_ids)
                    response["graph_contradictions"] = [dict(row) for row in await adapter.fetchall(
                        f"SELECT * FROM uie_graph_contradictions WHERE existing_relation_id IN ({relation_placeholders}) OR incoming_relation_id IN ({relation_placeholders}) ORDER BY created_at",
                        tuple(relation_ids + relation_ids))]
                    response["contradictions"].extend({"source": "knowledge_graph", **item} for item in response["graph_contradictions"])
                # Async reflexion: persist bounded caution rules through the same adapter.
                issues = []
                for edge in response["knowledge_graph"].get("relations", []):
                    edge_status = edge.get("status", "unverified_claim")
                    confidence = float(edge.get("confidence") or 0.0)
                    issue_type = None
                    if edge_status == "conflicted_evidence": issue_type = "conflicted_edge"
                    elif _is_stale(edge.get("temporal_anchor")): issue_type = "stale_temporal_anchor"
                    elif confidence < 0.45: issue_type = "low_confidence_edge"
                    if not issue_type: continue
                    issue = {"type": issue_type, "subject": str(edge.get("source_name") or subject),
                             "predicate": str(edge.get("predicate") or "related_to"),
                             "target": str(edge.get("target_name") or ""), "domain": str(edge.get("domain") or "general_reference"),
                             "provenance_url": edge.get("provenance_url"), "relation_id": edge.get("id")}
                    issue["rule_text"] = (f"Treat {issue['subject']} --{issue['predicate']}--> {issue['target']} as uncertain ({issue_type}); disclose uncertainty and seek independent, dated corroboration.")
                    stored = await self._astore_reflexion_issue(adapter, issue)
                    issues.append({**issue, **stored})
                response["graph_reflexion"] = {"issues": issues, "constraints_stored": issues,
                    "recalled_constraints": await self._arecall_reflexion_rules(adapter, subject),
                    "discovery": {"scheduled": False, "status": "discovery_trigger_evaluated_after_claim_query"},
                    "automatic_assertion_allowed": not bool(issues),
                    "note": "Constraints are cautionary heuristics, not factual resolution; extracted evidence remains unverified."}
            if trigger_learning and subject:
                low_confidence = any(float(c.get("confidence") or 0.0) < 0.60 for c in claims)
                flagged_claims = any(c.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"} for c in claims)
                unresolved_contradictions = any(str(item.get("resolution_status", "unresolved")).lower() == "unresolved"
                                                for item in contradiction_rows)
                sparse = len(claims) < 2
                should_discover = sparse or low_confidence or flagged_claims or unresolved_contradictions
                if should_discover:
                    from .epistemic_dispatcher import EpistemicDiscoveryDispatcher
                    dispatcher = object.__new__(EpistemicDiscoveryDispatcher)
                    dispatcher.db_path = str(self.memory.db_path)
                    dispatcher.seed_path = Path(__file__).resolve().parents[1] / "config" / "seeds.json"
                    reason = ("sparse_claim_evidence" if sparse else
                              "low_confidence_claim" if low_confidence else
                              "unresolved_contradiction" if unresolved_contradictions else "flagged_claim_status")
                    queued = await dispatcher.aenqueue(focus_entity=subject, query_text=subject,
                                                       reason=reason, storage=adapter)
                    response["learning_trigger"] = {"scheduled": bool(queued.get("enqueued")),
                        "status": queued.get("status", queued.get("reason", "not_scheduled")),
                        "reason": reason, "queue": queued,
                        "mode": "durable_async_queue", "background_worker": "external_dispatcher_required"}
                    if include_graph and "graph_reflexion" in response:
                        response["graph_reflexion"]["discovery"] = response["learning_trigger"]
                else:
                    response["learning_trigger"] = {"scheduled": False, "status": "knowledge_has_no_flagged_claims"}
            return response
        finally:
            if own_storage:
                await adapter.close()

    async def _astore_reflexion_issue(self, storage: StorageEngine, issue: dict[str, Any]) -> dict[str, Any]:
        from .heuristic_memory import HeuristicMemory
        # Async methods below are adapter-only; avoid HeuristicMemory.__init__ SQLite DDL.
        memory = object.__new__(HeuristicMemory)
        memory.db_path = str(self.memory.db_path)
        return await memory.astore_constraint(storage=storage, domain=issue["domain"], subject=issue["subject"],
            predicate=issue["predicate"], rule_text=issue["rule_text"],
            provenance_urls=[issue["provenance_url"]] if issue.get("provenance_url") else [],
            confidence=0.65, rule_type=issue["type"])

    async def _arecall_reflexion_rules(self, storage: StorageEngine, subject: str) -> list[dict[str, Any]]:
        from .heuristic_memory import HeuristicMemory
        memory = object.__new__(HeuristicMemory)
        memory.db_path = str(self.memory.db_path)
        rows = await memory.arecall_constraints(storage=storage, limit=100)
        query = subject.lower()
        return [row for row in rows if row.get("rule_type") in {"conflicted_edge", "stale_temporal_anchor", "low_confidence_edge"}
                and (str(row.get("subject", "")).lower() in query or query in str(row.get("subject", "")).lower())]

    def query(self, subject: str | None = None, limit: int = 20, trigger_learning: bool = False,
              include_graph: bool = True, max_hops: int = 2, min_confidence: float = 0.0,
              statuses: set[str] | None = None, domain: str | None = None) -> dict[str, Any]:
        """Deprecated synchronous legacy API; migrate callers to await aquery()."""
        deprecation_warnings.warn("ClaimQueryService.query() is deprecated; use await aquery() for async storage.",
                      DeprecationWarning, stacklevel=2)
        limit = max(1, min(int(limit), 100))
        all_claims = self.memory.get_claims()
        if subject:
            exact = [c for c in all_claims if subject.lower() in c.get("subject","").lower()]
            if exact:
                claims = exact
            else:
                claims = SemanticMatcher(threshold=0.18).match(subject, all_claims, threshold=0.18, limit=limit)
        else:
            claims = all_claims
        claims = [c for c in claims if float(c.get("confidence") or 0.0) >= max(0.0, min(1.0, float(min_confidence)))]
        if statuses is not None: claims = [c for c in claims if c.get("status") in statuses]
        if domain: claims = [c for c in claims if str(c.get("domain", "")).lower() == domain.strip().lower()]
        claims.sort(key=lambda c: (_valid_time(c.get("temporal_anchor")), str(c.get("created_at") or ""), str(c.get("id") or "")))
        claims = claims[-limit:]
        warnings = [
            {"claim_id": claim["id"], "status": claim["status"], "warning": claim["warning"]}
            for claim in claims if claim.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}
        ]
        response = {
            "subject_query": subject,
            "count": len(claims),
            "claims": [
                {key: claim.get(key) for key in (
                    "id", "subject", "predicate", "object_claim", "temporal_anchor", "confidence",
                    "provenance_url", "provenance_urls", "domain", "status", "warning", "created_at", "updated_at"
                )}
                for claim in claims
            ],
            "warnings": warnings,
            "usage_warning": "Extracted claims are evidence candidates, not verified facts. Check provenance and temporal scope before reasoning from them.",
        }
        if include_graph and subject:
            graph = KnowledgeGraphStore(self.memory.db_path).get_entity_subgraph(subject, max_hops=max_hops)
            graph_warnings = []
            for edge in graph.get("relations", []):
                if edge.get("status") in {"conflicted_evidence", "unverified_claim", "superseded_temporal"}:
                    graph_warnings.append({
                        "relation_id": edge.get("id"),
                        "status": edge.get("status"),
                        "warning": "Graph edge is evidence, not a verified fact.",
                        "provenance_url": edge.get("provenance_url"),
                    })
            response["knowledge_graph"] = graph
            response["graph_warnings"] = graph_warnings
            reflexion = ReflexiveGraphValidator(self.memory.db_path, learner=self.learner)
            response["graph_reflexion"] = reflexion.inspect(subject, graph, claims=all_claims)
        if trigger_learning and subject:
            if self.learner is None:
                from .autonomous_learner import AutonomousLearner
                self.learner = AutonomousLearner(claim_memory=self.memory)
            unresolved = not claims or bool(warnings)
            already_dispatched = bool(response.get("graph_reflexion", {}).get("discovery", {}).get("scheduled"))
            if unresolved and not already_dispatched:
                response["learning_trigger"] = self.learner.trigger_background(subject)
            else:
                response["learning_trigger"] = {"scheduled": False, "status": "knowledge_has_no_flagged_claims"}
        return response


def query_claims(subject: str | None = None, limit: int = 20, memory: ContradictionEngine | None = None,
                 trigger_learning: bool = False, learner=None, include_graph: bool = True,
                 max_hops: int = 2) -> dict[str, Any]:
    """Functional hook for reasoning code and tests."""
    return ClaimQueryService(memory=memory, learner=learner).query(
        subject=subject, limit=limit, trigger_learning=trigger_learning,
        include_graph=include_graph, max_hops=max_hops)


async def aquery_claims(subject: str | None = None, limit: int = 20, memory: ContradictionEngine | None = None,
                       learner=None, include_graph: bool = True, max_hops: int = 2,
                       storage: StorageEngine | None = None, min_confidence: float = 0.0,
                       statuses: set[str] | None = None, domain: str | None = None,
                       trigger_learning: bool = False) -> dict[str, Any]:
    """Async functional query hook; preferred for all async reasoning and ingestion callers."""
    return await ClaimQueryService(memory=memory, learner=learner).aquery(
        subject=subject, limit=limit, trigger_learning=trigger_learning,
        include_graph=include_graph, max_hops=max_hops, storage=storage,
        min_confidence=min_confidence, statuses=statuses, domain=domain)
