"""Bounded autonomous discovery, ingestion, claim evaluation and heuristic consolidation."""
from __future__ import annotations

import json
import asyncio
import warnings
import os
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from .contradiction import ContradictionEngine
from .curiosity_agent import CuriosityAgent
from .heuristic_memory import HeuristicMemory
from .query_claims import ClaimQueryService
from .cognitive_memory import CognitiveMemoryEngine
from .epistemic_dispatcher import EpistemicDiscoveryDispatcher
from .temporal_resolver import TemporalResolver
from ..ingestion.pipeline import CrossDomainIngestionPipeline
from ..web_knowledge import PublicWebKnowledgeStore
from ..security.provenance_crypto import append_integrity_event


class AutonomousLearner:
    MAX_DOMAINS = 3
    MAX_URLS_PER_DOMAIN = 5
    MAX_SECONDS = 60.0

    def __init__(self, pipeline: CrossDomainIngestionPipeline | None = None,
                 curiosity: CuriosityAgent | None = None,
                 claim_memory: ContradictionEngine | None = None,
                 heuristic_memory: HeuristicMemory | None = None,
                 timeout_seconds: float = 60.0):
        self.pipeline = pipeline or CrossDomainIngestionPipeline(PublicWebKnowledgeStore(os.getenv("UIE_WEB_KNOWLEDGE_PATH", ".data/web_knowledge.sqlite3")))
        self.claim_memory = claim_memory or self.pipeline.claim_memory
        self.query_service = ClaimQueryService(self.claim_memory)
        self.curiosity = curiosity or CuriosityAgent(self.query_service)
        self.heuristics = heuristic_memory or HeuristicMemory(self.claim_memory.db_path)
        self.timeout_seconds = min(self.MAX_SECONDS, max(1.0, float(timeout_seconds)))
        self._background_lock = threading.Lock()
        self._background_thread: threading.Thread | None = None
        self._last_background_result: dict | None = None

    @staticmethod
    def _independent_hosts(urls: list[str]) -> int:
        hosts = {(urlparse(url).hostname or "").lower().removeprefix("www.") for url in urls}
        return len({host for host in hosts if host})

    def _drain_epistemic_missions(self, max_entities: int = 2) -> dict:
        """Harvest at most two queued epistemic missions using only their reviewed seed URLs."""
        dispatcher = EpistemicDiscoveryDispatcher(
            self.claim_memory.db_path,
            seed_path=getattr(self.pipeline, "seed_path", None),
        )
        memory = CognitiveMemoryEngine(self.claim_memory.db_path)
        missions = dispatcher.claim_batch(max_entities)
        summary = {"missions_claimed": len(missions), "missions": [], "urls_processed": 0, "graph_relations_added": 0}
        for mission in missions[:2]:
            urls = list(dict.fromkeys(mission["seed_allowlist"]))[:5]
            enqueue_results = []
            for url in urls:
                try:
                    enqueue_results.append(self.pipeline.enqueue([url]))
                except Exception as exc:
                    enqueue_results.append({"error": type(exc).__name__})
            try:
                batch = self.pipeline.run_batch(limit=min(5, len(urls)), urls=urls)
            except TypeError:
                batch = self.pipeline.run_batch(limit=min(5, len(urls)))
            except Exception as exc:
                batch = {"error": type(exc).__name__, "results": []}
            relations_added = 0
            stored_urls = {item.get("url") for item in batch.get("results", []) if item.get("status") == "stored"}
            stored_urls |= {item.get("canonical_url") for item in batch.get("results", []) if item.get("status") == "stored"}
            claims = self.claim_memory.get_claims()
            for claim in claims:
                if claim.get("provenance_url") not in stored_urls:
                    continue
                try:
                    memory.graph.ingest_claim(claim, claim_id=str(claim.get("id") or "") or None)
                    relations_added += 1
                except (ValueError, TypeError):
                    continue
            try:
                memory.graph.sync_existing_claims()
                resolver = TemporalResolver(memory.graph)
                # Only explicitly dated, same-subject/same-predicate revisions are
                # eligible; TemporalResolver itself rejects unsupported supersession.
                all_claims = self.claim_memory.get_claims()
                harvested_claims = [claim for claim in all_claims if claim.get("provenance_url") in stored_urls]
                for newer in harvested_claims:
                    for older in all_claims:
                        if older.get("id") == newer.get("id"):
                            continue
                        if (str(older.get("subject", "")).casefold() != str(newer.get("subject", "")).casefold()
                                or str(older.get("predicate", "")).casefold() != str(newer.get("predicate", "")).casefold()):
                            continue
                        if str(older.get("object_claim", "")).casefold() == str(newer.get("object_claim", "")).casefold():
                            continue
                        resolver.resolve(
                            older, newer,
                            older_metadata=str(older.get("title", "")),
                            newer_metadata=" ".join((str(newer.get("title", "")),
                                                     str(newer.get("revision_header", "")))),
                        )
            except (ValueError, TypeError, OSError, KeyError):
                pass
            resolved = relations_added > 0
            result = {"urls_attempted": len(urls), "enqueue": enqueue_results, "batch": batch,
                      "graph_relations_added": relations_added, "resolved": resolved}
            dispatcher.finish(mission["id"], result, resolved=resolved)
            if resolved:
                now = datetime.now(timezone.utc).isoformat()
                with memory._connect() as conn:
                    rows = conn.execute(
                        "SELECT id,goal,feedback_json FROM uie_episodes WHERE lower(goal) LIKE ? ORDER BY created_at DESC LIMIT 20",
                        (f"%{mission['focus_entity'].lower()}%",),
                    ).fetchall()
                    for row in rows:
                        try:
                            feedback = json.loads(row["feedback_json"])
                        except (TypeError, json.JSONDecodeError):
                            feedback = {}
                        feedback["epistemic_resolution"] = {
                            "mission_id": mission["id"], "resolved_at": now,
                            "graph_relations_added": relations_added, "seed_urls": list(stored_urls)[:5],
                        }
                        conn.execute("UPDATE uie_episodes SET feedback_json=? WHERE id=?",
                                     (json.dumps(feedback, sort_keys=True), row["id"]))
                        append_integrity_event(conn, "episode", row["id"], "EPISTEMIC_DEFICIT_RESOLVED",
                            {"id": row["id"], "mission_id": mission["id"], "resolved_at": now,
                             "graph_relations_added": relations_added})
            summary["urls_processed"] += len(batch.get("results", []))
            summary["graph_relations_added"] += relations_added
            summary["missions"].append({"id": mission["id"], "focus_entity": mission["focus_entity"],
                                        "resolved": resolved, "graph_relations_added": relations_added,
                                        "processed": len(batch.get("results", []))})
        return summary

    def run_cycle(self, query: str) -> dict:
        warnings.warn("AutonomousLearner.run_cycle() is deprecated; use await arun_cycle().", DeprecationWarning, stacklevel=2)
        targets = self.curiosity.inspect(query, max_targets=self.MAX_DOMAINS)[:self.MAX_DOMAINS]
        return self._run_cycle_with_targets(query, targets)

    async def arun_cycle(self, query: str) -> dict:
        """Async-first cycle: query known claim gaps via aquery before bounded ingestion work."""
        targets = (await self.curiosity.ainspect(query, max_targets=self.MAX_DOMAINS))[:self.MAX_DOMAINS]
        return self._run_cycle_with_targets(query, targets)

    def _run_cycle_with_targets(self, query: str, targets: list[dict]) -> dict:
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        try:
            epistemic = self._drain_epistemic_missions(max_entities=2)
        except Exception as exc:
            epistemic = {"error": type(exc).__name__, "missions_claimed": 0, "missions": []}
        summary = {"query": query[:300], "targets": targets, "domains_processed": 0, "discovery": [], "ingestion": [], "heuristics": [], "epistemic_discovery": epistemic, "elapsed_seconds": 0.0, "timed_out": False}
        for target in targets:
            if time.monotonic() >= deadline:
                summary["timed_out"] = True
                break
            # Curiosity targets originate only from the reviewed seed catalog. Swap
            # the pipeline's seed view for this bounded call, then restore it.
            original_seed_path = getattr(self.pipeline, "seed_path", None)
            with tempfile.TemporaryDirectory(prefix="uie-curiosity-") as temp_dir:
                seed_file = Path(temp_dir) / "seeds.json"
                seed_file.write_text(json.dumps({target["domain"]: [target["seed_url"]]}), encoding="utf-8")
                try:
                    if original_seed_path is not None:
                        self.pipeline.seed_path = seed_file
                    remaining = max(0.1, deadline - time.monotonic())
                    discovered = self.pipeline.discover(max_sources=1, max_urls_per_source=self.MAX_URLS_PER_DOMAIN)
                except Exception as exc:
                    discovered = {"error": type(exc).__name__, "queued_new": 0}
                finally:
                    if original_seed_path is not None:
                        self.pipeline.seed_path = original_seed_path
            summary["discovery"].append({"domain": target["domain"], **discovered})
            if time.monotonic() >= deadline:
                summary["timed_out"] = True
                break
            # Worker enforces URL allowlists, robots, redirect checks, network timeout
            # and response caps. Hard cap is five queue items per target, 15 per cycle.
            try:
                remaining = max(0.1, deadline - time.monotonic())
                batch_limit = min(self.MAX_URLS_PER_DOMAIN, max(1, int(remaining // 5)))
                batch = self.pipeline.run_batch(limit=batch_limit)
            except Exception as exc:
                batch = {"error": type(exc).__name__, "results": []}
            summary["ingestion"].append({"domain": target["domain"], **batch})
            summary["domains_processed"] += 1
            for result in batch.get("results", []):
                if time.monotonic() >= deadline:
                    summary["timed_out"] = True
                    break
                if result.get("status") != "stored":
                    continue
                result_urls = {result.get("url"), result.get("canonical_url")}
                for claim in self.claim_memory.get_claims():
                    if claim.get("provenance_url") not in result_urls:
                        continue
                    hosts = self._independent_hosts(claim.get("provenance_urls") or [claim.get("provenance_url", "")])
                    if hosts >= 2:
                        consolidated = self.heuristics.consolidate(claim, independent_sources=hosts)
                        if consolidated.get("stored"):
                            summary["heuristics"].append(consolidated)
                    if claim.get("status") == "superseded_temporal":
                        temporal_rule = self.heuristics.consolidate(claim, independent_sources=1, rule_type="temporal_revision")
                        if temporal_rule.get("stored"):
                            summary["heuristics"].append(temporal_rule)
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["timed_out"] = summary["timed_out"] or summary["elapsed_seconds"] >= self.timeout_seconds
        summary["claim_memory_count"] = len(self.claim_memory.get_claims())
        summary["heuristic_count"] = len(self.heuristics.get_rules())
        return summary

    def trigger_background(self, query: str) -> dict:
        """Start at most one daemon learning cycle and return immediately."""
        with self._background_lock:
            if self._background_thread and self._background_thread.is_alive():
                return {"scheduled": False, "status": "already_running"}
            thread = threading.Thread(target=self._run_background, args=(query,), daemon=True, name="uie-autonomous-learning")
            self._background_thread = thread
            thread.start()
        return {"scheduled": True, "status": "started", "query": query[:300]}

    def _run_background(self, query: str) -> None:
        try:
            self._last_background_result = asyncio.run(self.arun_cycle(query))
        except Exception as exc:
            self._last_background_result = {"error": type(exc).__name__, "status": "failed"}

    def background_status(self) -> dict:
        thread = self._background_thread
        return {"running": bool(thread and thread.is_alive()), "last_result": self._last_background_result}
