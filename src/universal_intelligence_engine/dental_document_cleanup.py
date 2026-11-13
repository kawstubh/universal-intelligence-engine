"""Bounded cleanup for abandoned private dental document quarantine uploads.

This command never deletes verified clinical objects. It is a separate
server-side job, not a public API endpoint. Dry-run is the default; deletion
requires both DENTAL_DOCUMENTS_CLEANUP_ENABLED=true and the explicit --apply flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

MIN_PENDING_AGE_HOURS = 24
MAX_PENDING_AGE_HOURS = 720
MAX_CLEANUP_BATCH = 500


def _safe_pending_object_key(document: dict[str, Any]) -> bool:
    """Accept only the exact pending prefix generated for this document row."""
    key = str(document.get("upload_object_key") or "")
    if (
        not key
        or len(key) > 1024
        or key.startswith("/")
        or "\\" in key
        or document.get("object_key") is not None
    ):
        return False
    path = PurePosixPath(key)
    if path.as_posix() != key or any(part in {"", ".", ".."} for part in path.parts):
        return False

    expected_prefix = (
        f"pending/organisations/{document.get('organisation_id')}/"
        f"clinics/{document.get('clinic_id')}/"
        f"patients/{document.get('patient_id')}/{document.get('id')}/"
    )
    file_name = key[len(expected_prefix):] if key.startswith(expected_prefix) else ""
    expected_name = str(document.get("file_name") or "")
    return bool(file_name) and file_name == expected_name and "/" not in file_name and file_name not in {".", ".."}


def _key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _expired_documents(store, cutoff: str, limit: int, *, claim: bool) -> list[dict[str, Any]]:
    """Select old quarantine rows and atomically reject active pending states."""
    select_sql = (
        "SELECT id,organisation_id,clinic_id,patient_id,file_name,upload_object_key,object_key,status,updated_at "
        "FROM dental_documents WHERE status IN ('pending_upload','verifying','rejected') "
        "AND object_key IS NULL AND upload_object_key IS NOT NULL AND updated_at < %s "
        "ORDER BY updated_at ASC,id ASC LIMIT %s"
        if store.database_url
        else
        "SELECT id,organisation_id,clinic_id,patient_id,file_name,upload_object_key,object_key,status,updated_at "
        "FROM dental_documents WHERE status IN ('pending_upload','verifying','rejected') "
        "AND object_key IS NULL AND upload_object_key IS NOT NULL AND updated_at < ? "
        "ORDER BY updated_at ASC,id ASC LIMIT ?"
    )
    if claim and store.database_url:
        select_sql += " FOR UPDATE SKIP LOCKED"

    with store.transaction() as connection:
        if claim and not store.database_url:
            # A single cleanup runner takes the SQLite write lock before selecting rows.
            connection.execute("BEGIN IMMEDIATE")
        rows = store._execute(select_sql, (cutoff, limit), connection=connection)
        if not claim:
            return rows

        claimed: list[dict[str, Any]] = []
        update_sql = (
            "UPDATE dental_documents SET status='rejected' "
            "WHERE id=%s AND clinic_id=%s AND status IN ('pending_upload','verifying') "
            "AND object_key IS NULL AND upload_object_key=%s RETURNING id"
            if store.database_url
            else
            "UPDATE dental_documents SET status='rejected' "
            "WHERE id=? AND clinic_id=? AND status IN ('pending_upload','verifying') "
            "AND object_key IS NULL AND upload_object_key=? RETURNING id"
        )
        for row in rows:
            if row["status"] in ("pending_upload", "verifying"):
                changed = store._execute(
                    update_sql,
                    (row["id"], row["clinic_id"], row["upload_object_key"]),
                    connection=connection,
                )
                if not changed:
                    continue
                store.audit(
                    "system:document-cleanup",
                    "document.pending_upload_expired",
                    "document",
                    row["id"],
                    row["clinic_id"],
                    connection,
                )
                row["status"] = "rejected"
            claimed.append(row)
        return claimed


def _clear_deleted_quarantine_key(store, document: dict[str, Any], key: str) -> bool:
    now = datetime.now(UTC).isoformat()
    update_sql = (
        "UPDATE dental_documents SET upload_object_key=NULL,updated_at=%s "
        "WHERE id=%s AND clinic_id=%s AND status='rejected' AND object_key IS NULL "
        "AND upload_object_key=%s RETURNING id"
        if store.database_url
        else
        "UPDATE dental_documents SET upload_object_key=NULL,updated_at=? "
        "WHERE id=? AND clinic_id=? AND status='rejected' AND object_key IS NULL "
        "AND upload_object_key=? RETURNING id"
    )
    with store.transaction() as connection:
        rows = store._execute(
            update_sql,
            (now, document["id"], document["clinic_id"], key),
            connection=connection,
        )
        if rows:
            store.audit(
                "system:document-cleanup",
                "document.pending_upload_object_cleaned",
                "document",
                document["id"],
                document["clinic_id"],
                connection,
            )
        return bool(rows)


def cleanup_expired_pending_uploads(
    store,
    s3,
    bucket: str,
    *,
    older_than_hours: int = MIN_PENDING_AGE_HOURS,
    limit: int = 100,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reject and delete only old, unverified quarantine objects.

    A failed object deletion leaves the database key intact so the next
    scheduled run can retry. Verified objects are excluded in SQL and checked
    again before any delete call.
    """
    age_hours = int(older_than_hours)
    batch_limit = int(limit)
    if not MIN_PENDING_AGE_HOURS <= age_hours <= MAX_PENDING_AGE_HOURS:
        raise ValueError("Pending upload cleanup age must be between 24 hours and 30 days")
    if not 1 <= batch_limit <= MAX_CLEANUP_BATCH:
        raise ValueError("Document cleanup batch size must be between 1 and 500")
    if not str(bucket or "").strip():
        raise ValueError("A dedicated private document bucket is required")

    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Document cleanup time must be timezone-aware")
    cutoff = (current.astimezone(UTC) - timedelta(hours=age_hours)).isoformat()
    rows = _expired_documents(store, cutoff, batch_limit, claim=not dry_run)
    result = {
        "dry_run": bool(dry_run),
        "candidate_count": len(rows),
        "deleted_count": 0,
        "delete_failures": 0,
        "unsafe_keys": 0,
        "min_age_hours": age_hours,
    }

    for document in rows:
        key = str(document.get("upload_object_key") or "")
        if not _safe_pending_object_key(document):
            result["unsafe_keys"] += 1
            logger.warning(
                "Skipped an unsafe private document cleanup candidate (key_fingerprint=%s)",
                _key_fingerprint(key) if key else "missing",
            )
            continue
        if dry_run:
            continue
        if document.get("status") != "rejected":
            result["delete_failures"] += 1
            logger.warning(
                "Skipped a document cleanup candidate that was not atomically rejected (key_fingerprint=%s)",
                _key_fingerprint(key),
            )
            continue

        try:
            # S3 DeleteObject is idempotent; a missing quarantine object is safe.
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            result["delete_failures"] += 1
            logger.warning(
                "Private document quarantine deletion failed (key_fingerprint=%s, error_type=%s)",
                _key_fingerprint(key),
                type(exc).__name__,
            )
            continue

        try:
            cleared = _clear_deleted_quarantine_key(store, document, key)
        except Exception as exc:
            cleared = False
            logger.warning(
                "Private document cleanup metadata update failed (key_fingerprint=%s, error_type=%s)",
                _key_fingerprint(key),
                type(exc).__name__,
            )
        if not cleared:
            result["delete_failures"] += 1
            continue
        result["deleted_count"] += 1

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean abandoned private dental document quarantine uploads."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete eligible pending quarantine objects. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=MIN_PENDING_AGE_HOURS,
        help="Minimum age of an abandoned upload (24 hours to 30 days).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum objects to inspect in one run (1 to 500).",
    )
    args = parser.parse_args(argv)

    if os.getenv("DENTAL_DOCUMENTS_CLEANUP_ENABLED", "false").strip().lower() != "true":
        parser.error("DENTAL_DOCUMENTS_CLEANUP_ENABLED=true is required.")
    if not MIN_PENDING_AGE_HOURS <= args.min_age_hours <= MAX_PENDING_AGE_HOURS:
        parser.error("--min-age-hours must be between 24 and 720.")
    if not 1 <= args.limit <= MAX_CLEANUP_BATCH:
        parser.error("--limit must be between 1 and 500.")

    try:
        from .dental_documents import _get_s3_client, _storage_config
        from .dental_store import get_dental_store

        # Cleanup must work before the lifecycle attestation is enabled, but
        # private bucket and dedicated storage credentials remain mandatory.
        config = _storage_config(require_lifecycle=False)
        result = cleanup_expired_pending_uploads(
            get_dental_store(),
            _get_s3_client(config),
            config["bucket"],
            older_than_hours=args.min_age_hours,
            limit=args.limit,
            dry_run=not args.apply,
        )
    except Exception as exc:
        logger.error(
            "Private dental document cleanup aborted (error_type=%s)",
            type(exc).__name__,
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    return 1 if result["delete_failures"] or result["unsafe_keys"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
