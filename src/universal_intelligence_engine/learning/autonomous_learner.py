"""Bounded autonomous discovery, ingestion, claim evaluation and heuristic consolidation."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .contradiction import ContradictionEngine
from .curiosity_agent import CuriosityAgent
from .heuristic_memory import HeuristicMemory
from .query_claims import ClaimQueryService
from ..ingestion.pipeline import CrossDomainIngestionPipeline
from ..web_knowledge import PublicWebKnowledgeStore


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

    def run_cycle(self, query: str) -> dict:
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        targets = self.curiosity.inspect(query, max_targets=self.MAX_DOMAINS)[:self.MAX_DOMAINS]
        summary = {"query": query[:300], "targets": targets, "domains_processed": 0, "discovery": [], "ingestion": [], "heuristics": [], "elapsed_seconds": 0.0, "timed_out": False}
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
            self._last_background_result = self.run_cycle(query)
        except Exception as exc:
            self._last_background_result = {"error": type(exc).__name__, "status": "failed"}

    def background_status(self) -> dict:
        thread = self._background_thread
        return {"running": bool(thread and thread.is_alive()), "last_result": self._last_background_result}
