"""Opt-in, supervised async curiosity worker with a durable SQLite lease."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .autonomous_learner import AutonomousLearner


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class CuriosityScheduler:
    """Single active cycle across processes, crash-recoverable via expiring SQLite lease."""

    def __init__(self, learner: AutonomousLearner | None = None, *, state_path: str | None = None,
                 interval_seconds: float = 300, lease_seconds: float = 180, max_cycles: int | None = None):
        self.learner = learner or AutonomousLearner()
        self.state_path = state_path or os.getenv("UIE_SCHEDULER_STATE_PATH", ".data/learning_scheduler.sqlite3")
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True) if self.state_path != ":memory:" else None
        self.interval_seconds = max(60.0, float(interval_seconds))
        self.lease_seconds = max(120.0, float(lease_seconds))
        self.max_cycles = max_cycles
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._thread_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scheduler_lease (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, expires_at REAL, heartbeat_at TEXT);
                INSERT OR IGNORE INTO scheduler_lease(id,owner,expires_at,heartbeat_at) VALUES(1,NULL,0,NULL);
                CREATE TABLE IF NOT EXISTS scheduler_metrics (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL, cycles INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, skipped_overlap INTEGER NOT NULL DEFAULT 0,
                    last_started_at TEXT, last_finished_at TEXT, last_duration_seconds REAL, last_error TEXT, last_result_json TEXT, updated_at TEXT NOT NULL);
                INSERT OR IGNORE INTO scheduler_metrics(id,status,updated_at) VALUES(1,'idle','');
            """)

    def _connect(self):
        conn = sqlite3.connect(self.state_path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _acquire_lease(self) -> bool:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT owner,expires_at FROM scheduler_lease WHERE id=1").fetchone()
            if row["owner"] and row["owner"] != self.owner and float(row["expires_at"] or 0) > now:
                conn.execute("UPDATE scheduler_metrics SET skipped_overlap=skipped_overlap+1,updated_at=? WHERE id=1", (_utc(),))
                conn.execute("COMMIT")
                return False
            conn.execute("UPDATE scheduler_lease SET owner=?,expires_at=?,heartbeat_at=? WHERE id=1", (self.owner, now+self.lease_seconds, _utc()))
            conn.execute("UPDATE scheduler_metrics SET status='running',last_started_at=?,updated_at=? WHERE id=1", (_utc(), _utc()))
            conn.execute("COMMIT")
            return True

    def _release_lease(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE scheduler_lease SET owner=NULL,expires_at=0,heartbeat_at=? WHERE id=1 AND owner=?", (_utc(), self.owner))
            conn.execute("COMMIT")

    def run_once(self, query: str = "scheduled idle-time epistemic curiosity: unresolved, low-confidence, or stale knowledge") -> dict:
        if not self._acquire_lease():
            return {"status": "skipped_overlap", "scheduled": False}
        started = time.monotonic()
        try:
            # Existing learner enforces <=3 domains, <=5 queued URLs/domain and <=60s budget.
            result = self.learner.run_cycle(query[:300])
            status = "completed" if not result.get("timed_out") and not result.get("error") else "completed_with_warnings"
            error = result.get("error")
            return_value = {"status": status, "scheduled": True, "result": result}
            successes, failures = (1, 0) if status == "completed" else (0, 1)
        except Exception as exc:
            error = type(exc).__name__
            result = {"error": error, "status": "failed"}
            return_value = {"status": "failed", "scheduled": True, "result": result}
            successes, failures = 0, 1
        finally:
            duration = round(time.monotonic()-started, 3)
            # Record before releasing the lease so observers see a consistent terminal state.
            with self._connect() as conn:
                conn.execute("UPDATE scheduler_metrics SET status=?,cycles=cycles+1,successes=successes+?,failures=failures+?,last_finished_at=?,last_duration_seconds=?,last_error=?,last_result_json=?,updated_at=? WHERE id=1",
                    (return_value["status"], successes, failures, _utc(), duration, error, json.dumps(result, default=str)[:12000], _utc()))
            self._release_lease()
        return {**return_value, "duration_seconds": duration}

    async def run_forever(self) -> None:
        cycles = 0
        while not self._stop.is_set() and (self.max_cycles is None or cycles < self.max_cycles):
            await asyncio.to_thread(self.run_once)
            cycles += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run_forever(), name="uie-curiosity-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    def health(self) -> dict:
        with self._connect() as conn:
            metrics = dict(conn.execute("SELECT * FROM scheduler_metrics WHERE id=1").fetchone())
            lease = dict(conn.execute("SELECT owner,expires_at,heartbeat_at FROM scheduler_lease WHERE id=1").fetchone())
        if metrics.get("last_result_json"):
            try: metrics["last_result"] = json.loads(metrics.pop("last_result_json"))
            except json.JSONDecodeError: metrics["last_result"] = None
        else:
            metrics.pop("last_result_json", None)
            metrics["last_result"] = None
        return {"enabled": True, "running": bool(self._task and not self._task.done()), "metrics": metrics,
                "lease": {**lease, "active": bool(lease.get("owner") and float(lease.get("expires_at") or 0) > time.time())},
                "budget": {"max_seconds": 60, "max_domains": 3, "max_urls_per_domain": 5}}
