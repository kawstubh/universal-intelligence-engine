"""SQLite entity graph layered alongside the existing claim store."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def _guess_type(name: str) -> str:
    text = name.strip()
    if re.search(r"\b(19|20|21)\d{2}\b", text):
        return "time"
    if re.search(r"\b(inc|corp|company|university|organization|organisation|committee|agency)\b", text, re.I):
        return "organization"
    if re.search(r"\b(disease|syndrome|abscess|swelling|infection|cancer|diabetes)\b", text, re.I):
        return "condition"
    if re.search(r"\b(specification|standard|protocol|framework|technology|software)\b", text, re.I):
        return "concept"
    return "entity"


class KnowledgeGraphStore:
    """Store claim subject-predicate-object edges with provenance and bounded traversal."""

    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3", confidence_decay: float = 0.8):
        self.db_path = str(db_path)
        self.confidence_decay = min(1.0, max(0.0, float(confidence_decay)))
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS uie_entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS uie_relations (
                    id TEXT PRIMARY KEY,
                    source_entity_id TEXT NOT NULL REFERENCES uie_entities(id),
                    target_entity_id TEXT NOT NULL REFERENCES uie_entities(id),
                    predicate TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    provenance_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unverified_claim',
                    domain TEXT NOT NULL DEFAULT 'general_reference',
                    claim_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_uie_rel_source ON uie_relations(source_entity_id);
                CREATE INDEX IF NOT EXISTS idx_uie_rel_target ON uie_relations(target_entity_id);
                CREATE INDEX IF NOT EXISTS idx_uie_rel_predicate ON uie_relations(predicate);
                CREATE TABLE IF NOT EXISTS uie_graph_contradictions (
                    id TEXT PRIMARY KEY,
                    source_entity_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    existing_relation_id TEXT NOT NULL,
                    incoming_relation_id TEXT NOT NULL,
                    existing_target_entity_id TEXT NOT NULL,
                    incoming_target_entity_id TEXT NOT NULL,
                    detail_tag TEXT NOT NULL DEFAULT 'graph_edge_conflict',
                    created_at TEXT NOT NULL,
                    resolution_status TEXT NOT NULL DEFAULT 'unresolved'
                );
            """)
            # Migrate a prior version of these graph tables if it lacks optional metadata.
            rel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(uie_relations)")}
            for name, sql_type, default in (
                ("domain", "TEXT", "'general_reference'"),
                ("claim_id", "TEXT", "NULL"),
                ("created_at", "TEXT", "''"),
                ("updated_at", "TEXT", "''"),
            ):
                if name not in rel_columns:
                    conn.execute(f"ALTER TABLE uie_relations ADD COLUMN {name} {sql_type} DEFAULT {default}")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _entity(self, conn, name: str, domain: str, entity_type: str | None = None) -> str:
        clean = str(name).strip()
        normalized = _normal(clean)
        if not normalized:
            raise ValueError("entity name must not be empty")
        row = conn.execute("SELECT id FROM uie_entities WHERE normalized_name=?", (normalized,)).fetchone()
        if row:
            # Keep the original label, but fill a generic domain/type with more specific metadata.
            conn.execute(
                """UPDATE uie_entities SET
                   domain=CASE WHEN domain IN ('','general_reference') AND ? NOT IN ('','general_reference') THEN ? ELSE domain END,
                   entity_type=CASE WHEN entity_type='entity' AND ?!='entity' THEN ? ELSE entity_type END
                   WHERE id=?""",
                (domain, domain, entity_type or _guess_type(clean), entity_type or _guess_type(clean), row["id"]),
            )
            return row["id"]
        entity_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO uie_entities(id,name,entity_type,domain,created_at,normalized_name) VALUES(?,?,?,?,?,?)",
            (entity_id, clean, entity_type or _guess_type(clean), domain or "general_reference", _now(), normalized),
        )
        return entity_id

    def ingest_claim(self, claim: Any, *, claim_id: str | None = None,
                     subject_type: str | None = None, object_type: str | None = None) -> dict:
        """Ingest a DistilledClaim or mapping as a directed subject -> object relation."""
        get = (lambda key, default=None: getattr(claim, key, default)) if not isinstance(claim, dict) else claim.get
        subject = str(get("subject", "")).strip()
        predicate = str(get("predicate", "")).strip()
        object_name = str(get("object_claim", get("object", ""))).strip()
        if not subject or not predicate or not object_name:
            raise ValueError("claim requires non-empty subject, predicate, and object_claim")
        domain = str(get("domain", "general_reference") or "general_reference")
        provenance = str(get("provenance_url", "") or "")
        status = str(get("status", "unverified_claim") or "unverified_claim")
        # Automated public-web claims are provisional: never let a graph edge exceed 0.75.
        confidence = min(0.75, max(0.0, float(get("confidence", 0.0))))
        now = _now()
        with self._connect() as conn:
            source_id = self._entity(conn, subject, domain, subject_type)
            target_id = self._entity(conn, object_name, domain, object_type)
            conflicts = conn.execute(
                """SELECT * FROM uie_relations
                   WHERE source_entity_id=? AND lower(predicate)=lower(?) AND target_entity_id<>?
                     AND status NOT IN ('superseded_temporal','rejected')""",
                (source_id, predicate, target_id),
            ).fetchall()
            matching = conn.execute(
                """SELECT * FROM uie_relations WHERE source_entity_id=? AND lower(predicate)=lower(?)
                   AND target_entity_id=? AND status NOT IN ('superseded_temporal','rejected')""",
                (source_id, predicate, target_id),
            ).fetchall()
            incoming_host = (urlparse(provenance).hostname or "").lower().removeprefix("www.")
            for old_edge in matching:
                old_host = (urlparse(old_edge["provenance_url"]).hostname or "").lower().removeprefix("www.")
                if incoming_host and old_host and incoming_host != old_host:
                    # Asymptotic boost approaches, but never exceeds, the public-web cap.
                    boosted = min(0.75, float(old_edge["confidence"]) + 0.25 * (0.75 - float(old_edge["confidence"])))
                    conn.execute("UPDATE uie_relations SET confidence=?,updated_at=? WHERE id=?",
                                 (boosted, now, old_edge["id"]))
            relation_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO uie_relations(id,source_entity_id,target_entity_id,predicate,confidence,
                   provenance_url,status,domain,claim_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (relation_id, source_id, target_id, predicate, confidence, provenance, status,
                 domain, claim_id, now, now),
            )
            for old in conflicts:
                conn.execute("UPDATE uie_relations SET status='conflicted_evidence',updated_at=? WHERE id=?",
                             (now, old["id"]))
                conn.execute("UPDATE uie_relations SET status='conflicted_evidence',updated_at=? WHERE id=?",
                             (now, relation_id))
                conn.execute(
                    """INSERT INTO uie_graph_contradictions(id,source_entity_id,predicate,existing_relation_id,
                       incoming_relation_id,existing_target_entity_id,incoming_target_entity_id,detail_tag,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), source_id, predicate, old["id"], relation_id, old["target_entity_id"],
                     target_id, "graph_edge_conflict", now),
                )
            return {"id": relation_id, "source_entity_id": source_id, "target_entity_id": target_id,
                    "predicate": predicate, "confidence": confidence,
                    "status": "conflicted_evidence" if conflicts else status,
                    "contradictions_logged": len(conflicts), "claim_id": claim_id}

    def ingest_claims(self, claims) -> list[dict]:
        return [self.ingest_claim(claim) for claim in claims]

    def sync_existing_claims(self) -> dict:
        """Backfill graph edges from existing flat claims/uie_claims; safe to re-run."""
        rows = []
        with self._connect() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            source_tables = [name for name in ("claims", "uie_claims") if name in tables]
            for table in source_tables:
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if not {"id", "subject", "predicate"}.issubset(columns):
                    continue
                object_field = "object_claim" if "object_claim" in columns else ("object" if "object" in columns else None)
                if object_field is None:
                    continue
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
                    item = dict(row)
                    item["object_claim"] = item.get(object_field, "")
                    rows.append(item)
        synced = 0
        for row in rows:
            claim_id = str(row.get("id") or "")
            if not claim_id:
                continue
            with self._connect() as conn:
                exists = conn.execute("SELECT 1 FROM uie_relations WHERE claim_id=?", (claim_id,)).fetchone()
            if exists:
                continue
            self.ingest_claim(row, claim_id=claim_id)
            synced += 1
        return {"synced": synced}

    def get_entity_subgraph(self, entity_name: str, max_hops: int = 2) -> dict:
        """Return nodes and relations reachable within max_hops; confidence decays per hop."""
        hops = max(0, min(6, int(max_hops)))
        normalized = _normal(entity_name)
        with self._connect() as conn:
            start = conn.execute("SELECT * FROM uie_entities WHERE normalized_name=?", (normalized,)).fetchone()
            if not start:
                # Allow a unique substring match when callers use a shorter display label.
                candidates = conn.execute(
                    "SELECT * FROM uie_entities WHERE normalized_name LIKE ? ORDER BY length(normalized_name) LIMIT 2",
                    (f"%{normalized}%",),
                ).fetchall() if normalized else []
                start = candidates[0] if len(candidates) == 1 else None
            if not start:
                return {"root": None, "nodes": [], "relations": [], "max_hops": hops}
            nodes = {start["id"]: {**dict(start), "hop": 0}}
            relations = []
            relation_ids = set()
            queue = deque([(start["id"], 0)])
            while queue:
                current_id, depth = queue.popleft()
                if depth >= hops:
                    continue
                rows = conn.execute(
                    """SELECT r.*, s.name AS source_name, t.name AS target_name,
                       s.entity_type AS source_type, t.entity_type AS target_type,
                       s.domain AS source_domain, t.domain AS target_domain
                       FROM uie_relations r JOIN uie_entities s ON s.id=r.source_entity_id
                       JOIN uie_entities t ON t.id=r.target_entity_id
                       WHERE r.source_entity_id=? OR r.target_entity_id=?
                       ORDER BY r.created_at,r.id""",
                    (current_id, current_id),
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    if item["id"] not in relation_ids:
                        relation_ids.add(item["id"])
                        hop = depth + 1
                        item["hop"] = hop
                        item["raw_confidence"] = item["confidence"]
                        item["traversal_confidence"] = round(item["confidence"] * (self.confidence_decay ** (hop - 1)), 6)
                        relations.append(item)
                    other_id = item["target_entity_id"] if item["source_entity_id"] == current_id else item["source_entity_id"]
                    if other_id not in nodes:
                        node_row = conn.execute("SELECT * FROM uie_entities WHERE id=?", (other_id,)).fetchone()
                        if node_row:
                            nodes[other_id] = {**dict(node_row), "hop": depth + 1}
                            queue.append((other_id, depth + 1))
            contradictions = [dict(row) for row in conn.execute(
                "SELECT * FROM uie_graph_contradictions WHERE source_entity_id IN (%s) ORDER BY created_at" %
                (",".join("?" for _ in nodes) or "NULL"), tuple(nodes)
            )] if nodes else []
        return {"root": dict(start), "nodes": list(nodes.values()), "relations": relations,
                "contradictions": contradictions, "max_hops": hops, "confidence_decay": self.confidence_decay}

    def resolve_temporal_successor(self, *, subject: str, predicate: str, older_object: str,
                                   newer_object: str, older_claim_id: str | None = None,
                                   newer_claim_id: str | None = None) -> dict:
        """Supersede the older graph edge while retaining its row and provenance."""
        now = _now()
        with self._connect() as conn:
            source = conn.execute("SELECT id FROM uie_entities WHERE normalized_name=?", (_normal(subject),)).fetchone()
            old_target = conn.execute("SELECT id FROM uie_entities WHERE normalized_name=?", (_normal(older_object),)).fetchone()
            new_target = conn.execute("SELECT id FROM uie_entities WHERE normalized_name=?", (_normal(newer_object),)).fetchone()
            if not source or not old_target or not new_target:
                return {"updated": 0, "reason": "graph_edge_not_found"}
            sql = "UPDATE uie_relations SET status='superseded_temporal',updated_at=? WHERE source_entity_id=? AND lower(predicate)=lower(?) AND target_entity_id=? AND status!='superseded_temporal'"
            args = [now, source["id"], predicate, old_target["id"]]
            if older_claim_id:
                sql += " AND (claim_id=? OR claim_id IS NULL)"
                args.append(str(older_claim_id))
            cur = conn.execute(sql, args)
            # Remove stale conflict presentation for a pair resolved as a dated revision,
            # but keep contradiction rows as audit history with an explicit resolution marker.
            conn.execute("""UPDATE uie_graph_contradictions SET resolution_status='resolved_temporal_revision'
                WHERE source_entity_id=? AND lower(predicate)=lower(?) AND existing_target_entity_id=? AND incoming_target_entity_id=?""",
                (source["id"], predicate, old_target["id"], new_target["id"]))
            conn.execute("""UPDATE uie_graph_contradictions SET resolution_status='resolved_temporal_revision'
                WHERE source_entity_id=? AND lower(predicate)=lower(?) AND existing_target_entity_id=? AND incoming_target_entity_id=?""",
                (source["id"], predicate, new_target["id"], old_target["id"]))
        return {"updated": cur.rowcount, "older_object": older_object, "newer_object": newer_object,
                "status": "superseded_temporal", "audit_history_preserved": True}

    def get_contradictions(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM uie_graph_contradictions ORDER BY created_at DESC LIMIT ?", (max(1, min(1000, int(limit))),)
            )]
