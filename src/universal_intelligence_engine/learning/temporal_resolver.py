"""Resolve dated claim changes separately from ongoing factual disputes."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .graph_store import KnowledgeGraphStore

_REVISION = re.compile(r"\b(revis(?:ed|ion)|updated|amended|supersed(?:es|ed|ing)|replaced|version\s+\d|effective\s+from|correction|erratum|new edition|withdrawn|retracted)\b", re.I)
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_YEAR = re.compile(r"\b((?:19|20|21)\d{2})\b")


def parse_temporal(value: Any) -> dict:
    text = str(value or "").strip()
    match = _ISO_DATE.search(text)
    if match:
        try:
            return {"date": date.fromisoformat(match.group(1)), "precision": "day", "raw": text}
        except ValueError:
            pass
    years = _YEAR.findall(text)
    if years:
        return {"date": date(int(years[-1]), 1, 1), "precision": "year", "raw": text}
    return {"date": None, "precision": "unknown", "raw": text}


def is_revision(text: str | None) -> bool:
    return bool(_REVISION.search(text or ""))


class TemporalResolver:
    """Apply supersession only when later dating plus explicit revision evidence supports it."""

    def __init__(self, graph_store: KnowledgeGraphStore, minimum_date_delta_days: int = 0):
        self.graph = graph_store
        self.minimum_date_delta_days = max(0, int(minimum_date_delta_days))

    def resolve(self, older: dict, newer: dict, *, older_metadata: str = "", newer_metadata: str = "") -> dict:
        required = ("subject", "predicate", "object_claim")
        if any(not item.get(key) for item in (older, newer) for key in required):
            return {"resolved": False, "reason": "incomplete_claim"}
        if str(older["subject"]).casefold() != str(newer["subject"]).casefold() or str(older["predicate"]).casefold() != str(newer["predicate"]).casefold():
            return {"resolved": False, "reason": "timeline_mismatch"}
        if str(older["object_claim"]).casefold() == str(newer["object_claim"]).casefold():
            return {"resolved": False, "reason": "same_claim_not_conflict"}
        old_time = parse_temporal(older.get("temporal_anchor"))
        new_time = parse_temporal(newer.get("temporal_anchor"))
        old_date, new_date = old_time["date"], new_time["date"]
        if not old_date or not new_date or new_date <= old_date:
            return {"resolved": False, "reason": "no_later_temporal_anchor"}
        delta = (new_date - old_date).days
        revision_text = " ".join((newer_metadata, str(newer.get("revision_header", "")), str(newer.get("title", ""))))
        if delta < self.minimum_date_delta_days or not is_revision(revision_text):
            return {"resolved": False, "reason": "later_date_without_revision_evidence", "date_delta_days": delta}
        result = self.graph.resolve_temporal_successor(
            subject=str(older["subject"]), predicate=str(older["predicate"]),
            older_object=str(older["object_claim"]), newer_object=str(newer["object_claim"]),
            older_claim_id=str(older["id"]) if older.get("id") else None,
            newer_claim_id=str(newer["id"]) if newer.get("id") else None,
        )
        return {"resolved": result.get("updated", 0) > 0, "reason": "explicit_revision" if result.get("updated", 0) else result.get("reason", "graph_edge_not_found"),
                "date_delta_days": delta, "revision_evidence": revision_text, **result}
