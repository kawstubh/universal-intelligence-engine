"""Fail-closed sanitizer for untrusted retrieved text and tool payloads.

Sanitization reduces obvious indirect-instruction risk; it cannot prove content safe.
Downstream consumers must keep retrieved text in a data-only context.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .provenance_crypto import append_integrity_event, ensure_integrity_ledger


_PATTERNS = (
    re.compile(r"(?im)^.*(?:ignore|disregard|override|bypass)\s+(?:all\s+)?(?:previous|prior|system|developer|safety|guardrail|instructions?).*$"),
    re.compile(r"(?im)^.*(?:reveal|print|exfiltrate)\s+(?:the\s+)?(?:system prompt|hidden prompt|secrets?|signing key|credentials?).*$"),
    re.compile(r"(?im)^.*(?:doctor_validated|doctor signature|review token).*(?:forge|fake|override|bypass|set to).*$"),
    re.compile(r"(?im)^.*<\s*(?:system|assistant|developer)\s*>.*$"),
)


@dataclass(frozen=True)
class FilterResult:
    text: str
    flagged: bool
    event_id: str | None
    patterns: tuple[str, ...]
    original_sha256: str


def sanitize_untrusted_text(text: str, *, ledger_db_path: str | Path | None = None,
                             source: str = "untrusted_payload") -> FilterResult:
    if not isinstance(text, str):
        raise TypeError("untrusted text must be a string")
    original = text[:1_000_000]
    digest = hashlib.sha256(original.encode("utf-8", "replace")).hexdigest()
    flagged_patterns = []
    sanitized_lines = []
    for line in original.splitlines():
        matched = [str(i) for i, pattern in enumerate(_PATTERNS, start=1) if pattern.search(line)]
        if matched:
            flagged_patterns.extend(matched)
            sanitized_lines.append("[REMOVED: suspected indirect instruction]")
        else:
            sanitized_lines.append(line)
    sanitized = "\n".join(sanitized_lines)
    event_id = None
    if flagged_patterns and ledger_db_path is not None:
        path = str(ledger_db_path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            ensure_integrity_ledger(conn)
            event = append_integrity_event(conn, "adversarial_payload", digest[:24], "FLAGGED",
                {"source": str(source)[:256], "original_sha256": digest,
                 "removed_line_count": len(flagged_patterns), "pattern_ids": sorted(set(flagged_patterns)),
                 "status": "untrusted_sanitized"})
            event_id = str(event["sequence_id"])
    return FilterResult(sanitized, bool(flagged_patterns), event_id,
                        tuple(sorted(set(flagged_patterns))), digest)
