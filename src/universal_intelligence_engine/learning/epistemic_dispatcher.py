"""Bounded epistemic-void detection and durable discovery mission dispatch."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..security.provenance_crypto import append_integrity_event, ensure_integrity_ledger, canonical_json, GENESIS_HASH


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entity_key(value: str) -> str:
    return re.sub(r"\\s+", " ", value.strip().casefold())


class EpistemicDiscoveryDispatcher:
    """Enqueue safe discovery missions; network work is left to the opt-in scheduler."""

    MAX_TARGET_DOMAINS = 3
    MAX_SEED_URLS = 5
    DEDUP_WINDOW = timedelta(hours=24)

    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3",
                 seed_path: str | Path | None = None):
        self.db_path = str(db_path)
        self.seed_path = Path(seed_path) if seed_path else Path(__file__).resolve().parents[1] / "config" / "seeds.json"
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            ensure_integrity_ledger(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS uie_discovery_missions (
                id TEXT PRIMARY KEY, focus_entity TEXT NOT NULL, entity_key TEXT NOT NULL,
                query_text TEXT NOT NULL, target_domains_json TEXT NOT NULL,
                seed_allowlist_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result_json TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_uie_discovery_status ON uie_discovery_missions(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_uie_discovery_entity ON uie_discovery_missions(entity_key, created_at)")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _approved_seeds(self, query_text: str, target_domains: list[str] | None = None) -> tuple[list[str], list[str]]:
        catalog = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
        lowered = query_text.lower()
        categories = {
            "health_life_sciences": ("health", "clinical", "medicine", "disease", "patient", "who", "medical"),
            "defence_geopolitics": ("defence", "defense", "military", "war", "geopolitic", "nato"),
            "politics_government_countries": ("government", "regulation", "law", "policy", "country", "india", "united nations"),
            "technology_science": ("technology", "software", "science", "standard", "protocol", "ai", "computing"),
            "markets_finance_industry": ("economy", "market", "finance", "revenue", "inflation", "rbi", "stock"),
            "daily_needs_agriculture_environment": ("climate", "environment", "agriculture", "energy", "food", "weather"),
            "news_current_affairs": ("news", "current", "latest", "today", "election"),
        }
        selected = [d for d in (target_domains or []) if d in catalog]
        if not selected:
            selected = [key for key, words in categories.items() if any(word in lowered for word in words)]
        if not selected:
            selected = ["technology_science"]
        urls: list[str] = []
        valid_domains: list[str] = []
        for domain in selected[:self.MAX_TARGET_DOMAINS]:
            for url in catalog.get(domain, []):
                parsed = urlparse(url)
                if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443):
                    urls.append(url)
                    valid_domains.append(domain)
                    if len(urls) >= self.MAX_SEED_URLS:
                        return list(dict.fromkeys(valid_domains)), list(dict.fromkeys(urls))
        return list(dict.fromkeys(valid_domains)), list(dict.fromkeys(urls))[:self.MAX_SEED_URLS]

    @staticmethod
    def focus_entity(query_text: str, context: dict | None = None) -> str:
        explicit = (context or {}).get("focus_entity")
        if isinstance(explicit, str) and explicit.strip():
            return re.sub(r"\\s+", " ", explicit.strip())[:160]
        # Keep the entity label bounded and avoid turning the entire prompt into an entity.
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*", query_text)
        return " ".join(words[:4])[:160] or "unresolved-query"

    def enqueue(self, *, focus_entity: str, query_text: str, target_domains: list[str] | None = None,
                seed_allowlist: list[str] | None = None, reason: str = "low_evidence_grounding") -> dict:
        entity = re.sub(r"\\s+", " ", str(focus_entity).strip())[:160]
        if not entity or not query_text.strip():
            return {"enqueued": False, "reason": "missing_entity_or_query"}
        domains, approved_urls = self._approved_seeds(query_text, target_domains)
        # A caller may narrow the allowlist but cannot add arbitrary hosts.
        if seed_allowlist is not None:
            approved_set = set(approved_urls)
            approved_urls = [url for url in seed_allowlist if url in approved_set][:self.MAX_SEED_URLS]
        if not approved_urls:
            return {"enqueued": False, "reason": "no_approved_seeds"}
        now = _now()
        key = _entity_key(entity)
        with self._connect() as conn:
            cutoff = (now - self.DEDUP_WINDOW).isoformat()
            existing = conn.execute("""SELECT id,status,created_at FROM uie_discovery_missions
                WHERE entity_key=? AND created_at>=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1""",
                (key, cutoff)).fetchone()
            if existing:
                return {"enqueued": False, "reason": "duplicate_active_mission", "mission_id": existing["id"]}
            mission_id = str(uuid.uuid4())
            payload = {"id": mission_id, "focus_entity": entity, "entity_key": key, "query_text": query_text[:500],
                       "target_domains": domains, "seed_allowlist": approved_urls, "reason": reason,
                       "created_at": now.isoformat()}
            conn.execute("""INSERT INTO uie_discovery_missions
                (id,focus_entity,entity_key,query_text,target_domains_json,seed_allowlist_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'queued',?,?)""",
                (mission_id, entity, key, query_text[:500], json.dumps(domains), json.dumps(approved_urls),
                 now.isoformat(), now.isoformat()))
            append_integrity_event(conn, "discovery_mission", mission_id, "DISCOVERY_ENQUEUED", payload)
        return {"enqueued": True, "mission_id": mission_id, "focus_entity": entity,
                "target_domains": domains, "seed_allowlist": approved_urls, "status": "queued"}


    async def aenqueue(self, *, focus_entity: str, query_text: str, target_domains: list[str] | None = None,
                       seed_allowlist: list[str] | None = None, reason: str = "low_evidence_grounding",
                       storage=None) -> dict:
        """Durably enqueue a bounded discovery mission using the async storage backend.

        Queue row and integrity-ledger event commit atomically. PostgreSQL serializes
        ledger-head allocation with a transaction-scoped advisory lock; SQLite uses the
        StorageEngine's writer transaction lock. This runs after the read query's selects.
        """
        import hashlib
        import uuid
        if storage is None:
            raise ValueError("a connected StorageEngine is required for async enqueue")
        entity = re.sub(r"\s+", " ", str(focus_entity).strip())[:160]
        if not entity or not query_text.strip():
            return {"enqueued": False, "reason": "missing_entity_or_query"}
        domains, approved_urls = self._approved_seeds(query_text, target_domains)
        if seed_allowlist is not None:
            approved_set = set(approved_urls)
            approved_urls = [url for url in seed_allowlist if url in approved_set][:self.MAX_SEED_URLS]
        if not approved_urls:
            return {"enqueued": False, "reason": "no_approved_seeds"}
        now = _now()
        key = _entity_key(entity)
        async with storage.transaction() as tx:
            if storage.dialect == "postgresql":
                await tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("uie-integrity-ledger",))
            cutoff = (now - self.DEDUP_WINDOW).isoformat()
            existing = await tx.fetchone("SELECT id,status,created_at FROM uie_discovery_missions WHERE entity_key=? AND created_at>=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (key, cutoff))
            if existing:
                return {"enqueued": False, "reason": "duplicate_active_mission", "mission_id": existing["id"]}
            mission_id = str(uuid.uuid4())
            payload = {"id": mission_id, "focus_entity": entity, "entity_key": key, "query_text": query_text[:500],
                       "target_domains": domains, "seed_allowlist": approved_urls, "reason": reason,
                       "created_at": now.isoformat()}
            await tx.execute("INSERT INTO uie_discovery_missions (id,focus_entity,entity_key,query_text,target_domains_json,seed_allowlist_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)",
                (mission_id, entity, key, query_text[:500], json.dumps(domains), json.dumps(approved_urls), now.isoformat(), now.isoformat()))
            head = await tx.fetchone("SELECT sequence_id,record_hash FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1")
            previous = head["record_hash"] if head else GENESIS_HASH
            if storage.dialect == "postgresql":
                allocated = await tx.fetchone("SELECT nextval(pg_get_serial_sequence('uie_integrity_ledger','sequence_id')) AS sequence_id")
                sequence_id = int(allocated["sequence_id"])
            else:
                sequence_id = int(head["sequence_id"]) + 1 if head else 1
            ledger_payload = {"sequence_id": sequence_id, "entity_type": "discovery_mission", "entity_id": mission_id,
                              "event_type": "DISCOVERY_ENQUEUED", "payload": payload}
            digest = hashlib.sha256(canonical_json(ledger_payload).encode("utf-8") + previous.encode("ascii")).hexdigest()
            await tx.execute("INSERT INTO uie_integrity_ledger(sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?,?)",
                (sequence_id, "discovery_mission", mission_id, "DISCOVERY_ENQUEUED", canonical_json(payload), previous, digest))
        return {"enqueued": True, "mission_id": mission_id, "focus_entity": entity,
                "target_domains": domains, "seed_allowlist": approved_urls, "status": "queued",
                "ledger_sequence_id": sequence_id, "ledger_record_hash": digest}

    def enqueue_void(self, *, query_text: str, context: dict | None = None, reason: str = "low_evidence_grounding") -> dict:
        entity = self.focus_entity(query_text, context)
        return self.enqueue(focus_entity=entity, query_text=query_text, target_domains=(context or {}).get("target_domains"),
                            reason=reason)

    def claim_batch(self, limit: int = 2) -> list[dict]:
        limit = max(1, min(int(limit), 2))
        now = _now().isoformat()
        claimed = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM uie_discovery_missions WHERE status='queued' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
            for row in rows:
                updated = conn.execute("UPDATE uie_discovery_missions SET status='running',updated_at=? WHERE id=? AND status='queued'", (now, row["id"]))
                if not updated.rowcount:
                    continue
                claimed.append({"id": row["id"], "focus_entity": row["focus_entity"], "query_text": row["query_text"],
                    "target_domains": json.loads(row["target_domains_json"]),
                    "seed_allowlist": json.loads(row["seed_allowlist_json"])[:self.MAX_SEED_URLS]})
                append_integrity_event(conn, "discovery_mission", row["id"], "DISCOVERY_STARTED",
                    {"id": row["id"], "status": "running", "updated_at": now})
            conn.commit()
        return claimed

    def finish(self, mission_id: str, result: dict, *, resolved: bool) -> None:
        now = _now().isoformat()
        status = "resolved" if resolved else "completed"
        payload = {"id": mission_id, "status": status, "result": result, "updated_at": now}
        with self._connect() as conn:
            conn.execute("UPDATE uie_discovery_missions SET status=?,updated_at=?,result_json=? WHERE id=?",
                         (status, now, json.dumps(result, default=str)[:12000], mission_id))
            append_integrity_event(conn, "discovery_mission", mission_id, "DISCOVERY_RESOLVED" if resolved else "DISCOVERY_COMPLETED", payload)

    def list_missions(self, status: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM uie_discovery_missions WHERE status=? ORDER BY created_at", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM uie_discovery_missions ORDER BY created_at").fetchall()
        return [{"id": r["id"], "focus_entity": r["focus_entity"], "query_text": r["query_text"],
                 "target_domains": json.loads(r["target_domains_json"]), "seed_allowlist": json.loads(r["seed_allowlist_json"]),
                 "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                 "result": json.loads(r["result_json"]) if r["result_json"] else None} for r in rows]
