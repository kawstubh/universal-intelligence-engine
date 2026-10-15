"""Persistent conservative domain heuristics distilled from independent evidence."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from ..security.provenance_crypto import GENESIS_HASH, calculate_record_hash, append_integrity_event, ensure_integrity_ledger, canonical_json
from ..db.storage_engine import StorageEngine


class HeuristicMemory:
    """Store auditable heuristics; unreviewed public evidence confidence is capped at 0.75."""

    def __init__(self, db_path: str | Path = ".data/claim_memory.sqlite3"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS heuristic_rules (
                id TEXT PRIMARY KEY, domain TEXT NOT NULL, subject TEXT NOT NULL,
                predicate TEXT NOT NULL, rule_text TEXT NOT NULL, confidence REAL NOT NULL,
                provenance_urls TEXT NOT NULL, rule_type TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, prev_hash TEXT, record_hash TEXT,
                UNIQUE(domain, subject, predicate, rule_type)
            )""")
            columns={row["name"] for row in conn.execute("PRAGMA table_info(heuristic_rules)")}
            for name in ("prev_hash","record_hash"):
                if name not in columns: conn.execute(f"ALTER TABLE heuristic_rules ADD COLUMN {name} TEXT")
            ensure_integrity_ledger(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_chain (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, prev_hash TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _consolidate_untracked(self, claim: dict, independent_sources: int | None = None, rule_type: str = "corroborated_claim") -> dict:
        urls = list(dict.fromkeys(claim.get("provenance_urls") or [claim.get("provenance_url", "")]))
        urls = [u for u in urls if u]
        sources = independent_sources if independent_sources is not None else len(urls)
        if sources < 2 and rule_type != "temporal_revision":
            return {"stored": False, "reason": "requires_independent_sources"}
        confidence = min(0.75, max(0.0, float(claim.get("confidence", 0.0))))
        if rule_type == "temporal_revision":
            rule = f"Prefer the newer dated evidence for {claim['subject']} / {claim['predicate']}; retain older versions as superseded and preserve provenance."
        else:
            rule = f"Treat {claim['subject']} / {claim['predicate']} as provisionally supported only when independently sourced; re-check time scope and contradictions."
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM heuristic_rules WHERE domain=? AND subject=? AND predicate=? AND rule_type=?", (claim.get("domain", "general_reference"), claim["subject"], claim["predicate"], rule_type)).fetchone()
            if existing:
                merged = list(dict.fromkeys(json.loads(existing["provenance_urls"]) + urls))
                confidence = min(0.75, max(confidence, float(existing["confidence"])))
                conn.execute("UPDATE heuristic_rules SET rule_text=?,confidence=?,provenance_urls=?,updated_at=? WHERE id=?", (rule, confidence, json.dumps(merged), now, existing["id"]))
                return {"stored": True, "id": existing["id"], "confidence": confidence, "action": "updated"}
            rule_id = str(uuid.uuid4())
            conn.execute("INSERT INTO heuristic_rules(id,domain,subject,predicate,rule_text,confidence,provenance_urls,rule_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (rule_id, claim.get("domain", "general_reference"), claim["subject"], claim["predicate"], rule, confidence, json.dumps(urls), rule_type, now, now))
        return {"stored": True, "id": rule_id, "confidence": confidence, "action": "inserted"}

    def consolidate(self, claim: dict, independent_sources: int | None = None, rule_type: str = "corroborated_claim") -> dict:
        result=self._consolidate_untracked(claim,independent_sources,rule_type)
        if result.get("stored") and result.get("id"):
            with self._connect() as conn:
                row=conn.execute("SELECT * FROM heuristic_rules WHERE id=?",(result["id"],)).fetchone()
                item=dict(row)
                payload={k:item.get(k) for k in ("id","domain","subject","predicate","rule_text","confidence","provenance_urls","rule_type","created_at","updated_at")}
                head=conn.execute("SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1").fetchone()
                previous=head["record_hash"] if head else GENESIS_HASH
                wrapped={"record_type":"heuristic","record_id":result["id"],"payload":payload}
                digest=calculate_record_hash(wrapped,previous); now=datetime.now(timezone.utc).isoformat()
                conn.execute("INSERT INTO memory_chain(record_type,record_id,payload_json,prev_hash,record_hash,created_at) VALUES(?,?,?,?,?,?)",
                    ("heuristic",result["id"],json.dumps(wrapped,sort_keys=True,separators=(",",":"),ensure_ascii=False),previous,digest,now))
                conn.execute("UPDATE heuristic_rules SET prev_hash=?,record_hash=? WHERE id=?",(previous,digest,result["id"]))
                append_integrity_event(conn, "heuristic", result["id"], "CONFIDENCE_UPDATE" if result.get("action") == "updated" else "INSERT", payload)
        return result


    def store_constraint(self, *, domain: str, subject: str, predicate: str,
                         rule_text: str, provenance_urls: list[str] | None = None,
                         confidence: float = 0.65, rule_type: str = "graph_constraint") -> dict:
        """Persist an idempotent negative constraint without requiring corroboration."""
        urls = list(dict.fromkeys(url for url in (provenance_urls or []) if url))
        confidence = min(0.75, max(0.0, float(confidence)))
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM heuristic_rules WHERE domain=? AND subject=? AND predicate=? AND rule_type=?",
                (domain, subject, predicate, rule_type),
            ).fetchone()
            if row:
                merged_urls = list(dict.fromkeys(json.loads(row["provenance_urls"]) + urls))
                confidence = min(0.75, max(confidence, float(row["confidence"])))
                conn.execute(
                    "UPDATE heuristic_rules SET rule_text=?,confidence=?,provenance_urls=?,updated_at=? WHERE id=?",
                    (rule_text, confidence, json.dumps(merged_urls), now, row["id"]),
                )
                rule_id, action = row["id"], "updated"
            else:
                rule_id, action = str(uuid.uuid4()), "inserted"
                conn.execute(
                    "INSERT INTO heuristic_rules(id,domain,subject,predicate,rule_text,confidence,provenance_urls,rule_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rule_id, domain, subject, predicate, rule_text, confidence, json.dumps(urls), rule_type, now, now),
                )
            item = dict(conn.execute("SELECT * FROM heuristic_rules WHERE id=?", (rule_id,)).fetchone())
            payload = {k: item.get(k) for k in ("id","domain","subject","predicate","rule_text","confidence","provenance_urls","rule_type","created_at","updated_at")}
            head = conn.execute("SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = head["record_hash"] if head else GENESIS_HASH
            wrapped = {"record_type":"heuristic", "record_id":rule_id, "payload":payload}
            digest = calculate_record_hash(wrapped, previous)
            conn.execute("INSERT INTO memory_chain(record_type,record_id,payload_json,prev_hash,record_hash,created_at) VALUES(?,?,?,?,?,?)",
                         ("heuristic", rule_id, json.dumps(wrapped, sort_keys=True, separators=(",",":"), ensure_ascii=False), previous, digest, now))
            conn.execute("UPDATE heuristic_rules SET prev_hash=?,record_hash=? WHERE id=?", (previous, digest, rule_id))
            append_integrity_event(conn, "heuristic", rule_id, "CONFIDENCE_UPDATE" if action == "updated" else "INSERT", payload)
        return {"stored": True, "id": rule_id, "confidence": confidence, "action": action, "rule_type": rule_type}

    def get_rules(self, domain: str | None = None) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM heuristic_rules WHERE (? IS NULL OR domain=?) ORDER BY updated_at DESC", (domain, domain)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provenance_urls"] = json.loads(item["provenance_urls"])
            result.append(item)
        return result

    async def astore_constraint(self, *, storage: StorageEngine, domain: str, subject: str, predicate: str,
                                rule_text: str, provenance_urls: list[str] | None = None,
                                confidence: float = 0.65, rule_type: str = "graph_constraint") -> dict:
        """Async transactional constraint write; rule, memory chain and audit ledger commit together."""
        urls = list(dict.fromkeys(url for url in (provenance_urls or []) if url))
        confidence = min(0.75, max(0.0, float(confidence)))
        now = datetime.now(timezone.utc).isoformat()
        async with storage.transaction() as tx:
            if storage.dialect == "postgresql":
                await tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("uie-ledger-chain",))
            row = await tx.fetchone("SELECT * FROM heuristic_rules WHERE domain=? AND subject=? AND predicate=? AND rule_type=? ORDER BY updated_at DESC LIMIT 1", (domain, subject, predicate, rule_type))
            if row:
                rule_id = row["id"]
                merged_urls = list(dict.fromkeys(json.loads(row["provenance_urls"] or "[]") + urls))
                confidence = min(0.75, max(confidence, float(row["confidence"])))
                await tx.execute("UPDATE heuristic_rules SET rule_text=?,confidence=?,provenance_urls=?,updated_at=? WHERE id=?", (rule_text, confidence, canonical_json(merged_urls), now, rule_id))
                action = "updated"
            else:
                rule_id, action = str(uuid.uuid4()), "inserted"
                await tx.execute("INSERT INTO heuristic_rules(id,domain,subject,predicate,rule_text,confidence,provenance_urls,rule_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (rule_id, domain, subject, predicate, rule_text, confidence, canonical_json(urls), rule_type, now, now))
            row = await tx.fetchone("SELECT * FROM heuristic_rules WHERE id=?", (rule_id,))
            payload = {k: row[k] for k in ("id","domain","subject","predicate","rule_text","confidence","provenance_urls","rule_type","created_at","updated_at")}
            wrapped = {"record_type":"heuristic", "record_id":rule_id, "payload":payload}
            head = await tx.fetchone("SELECT record_hash FROM memory_chain ORDER BY sequence DESC LIMIT 1")
            previous = head["record_hash"] if head else GENESIS_HASH
            digest = calculate_record_hash(wrapped, previous)
            await tx.execute("INSERT INTO memory_chain(record_type,record_id,payload_json,prev_hash,record_hash,created_at) VALUES(?,?,?,?,?,?)", ("heuristic",rule_id,canonical_json(wrapped),previous,digest,now))
            if storage.dialect == "postgresql":
                await tx.execute("SELECT setval(pg_get_serial_sequence('memory_chain','sequence'), ?, true)", (int((await tx.fetchone("SELECT MAX(sequence) AS sequence FROM memory_chain"))["sequence"]),))
            await tx.execute("UPDATE heuristic_rules SET prev_hash=?,record_hash=? WHERE id=?", (previous,digest,rule_id))
            ledger_head = await tx.fetchone("SELECT sequence_id,record_hash FROM uie_integrity_ledger ORDER BY sequence_id DESC LIMIT 1")
            seq = int(ledger_head["sequence_id"])+1 if ledger_head else 1
            ledger_prev = ledger_head["record_hash"] if ledger_head else GENESIS_HASH
            event = "CONFIDENCE_UPDATE" if action == "updated" else "INSERT"
            ledger_payload = canonical_json(payload)
            ledger_hash_payload = {"sequence_id":seq,"entity_type":"heuristic","entity_id":rule_id,"event_type":event,"payload":json.loads(ledger_payload)}
            ledger_digest = __import__("hashlib").sha256(canonical_json(ledger_hash_payload).encode("utf-8")+ledger_prev.encode("ascii")).hexdigest()
            await tx.execute("INSERT INTO uie_integrity_ledger(sequence_id,entity_type,entity_id,event_type,payload_canonical_json,prev_hash,record_hash) VALUES(?,?,?,?,?,?,?)", (seq,"heuristic",rule_id,event,ledger_payload,ledger_prev,ledger_digest))
            if storage.dialect == "postgresql":
                await tx.execute("SELECT setval(pg_get_serial_sequence('uie_integrity_ledger','sequence_id'), ?, true)", (seq,))
        return {"stored":True,"id":rule_id,"confidence":confidence,"action":action,"rule_type":rule_type}

    async def arecall_constraints(self, *, storage: StorageEngine, domain: str | None = None,
                                  subject: str | None = None, rule_type: str | None = None,
                                  limit: int = 100) -> list[dict]:
        """Read rules through the async storage adapter, never sqlite3 directly."""
        clauses=[]; params=[]
        for column, value in (("domain",domain),("subject",subject),("rule_type",rule_type)):
            if value is not None:
                clauses.append(f"{column}=?"); params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = await storage.fetchall("SELECT * FROM heuristic_rules"+where+" ORDER BY updated_at DESC LIMIT ?", (*params,max(1,min(int(limit),500))))
        result=[]
        for row in rows:
            item=dict(row)
            try: item["provenance_urls"] = json.loads(item.get("provenance_urls") or "[]")
            except (TypeError,json.JSONDecodeError): item["provenance_urls"]=[]
            result.append(item)
        return result
