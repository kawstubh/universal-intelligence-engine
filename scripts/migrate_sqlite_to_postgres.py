"""One-time legacy SQLite -> PostgreSQL migration helper.

Usage:
  $env:DATABASE_URL = "<Render internal/external URL>"
  $env:DENTAL_SQLITE_SOURCE = "dental.sqlite3"
  python scripts/migrate_sqlite_to_postgres.py

The script only copies rows. It never prints DATABASE_URL or patient content.
Run Alembic first so the PostgreSQL schema exists.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import psycopg


TABLES = [
    "dental_patients",
    "dental_appointments",
    "dental_chart_entries",
    "dental_periodontal_entries",
    "dental_ai_events",
    "dental_otp_challenges",
    "dental_sessions",
    "dental_audit_events",
    "dental_doctors",
    "dental_membership_orders",
    "dental_memberships",
]


def main() -> None:
    source = Path(os.getenv("DENTAL_SQLITE_SOURCE", "dental.sqlite3"))
    destination = os.getenv("DATABASE_URL")
    if not source.exists():
        raise SystemExit(f"SQLite source not found: {source}")
    if not destination:
        raise SystemExit("DATABASE_URL is required")

    sqlite = sqlite3.connect(source)
    sqlite.row_factory = sqlite3.Row
    pg = psycopg.connect(destination)

    try:
        with pg:
            with pg.cursor() as cur:
                total = 0
                for table in TABLES:
                    exists = sqlite.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if not exists:
                        continue
                    columns = [r[1] for r in sqlite.execute(f"PRAGMA table_info({table})").fetchall()]
                    rows = sqlite.execute(f"SELECT * FROM {table}").fetchall()
                    if not rows:
                        continue

                    # Legacy databases did not have clinic_id. Assign the explicit
                    # migration clinic only when it is absent.
                    if "clinic_id" not in columns and table in {
                        "dental_patients", "dental_appointments",
                        "dental_chart_entries", "dental_periodontal_entries",
                        "dental_ai_events",
                    }:
                        columns.append("clinic_id")
                        default_clinic = os.getenv("DENTAL_MASTER_CLINIC_ID", "dr-pranali")
                        rows = [tuple(row) + (default_clinic,) for row in rows]
                    else:
                        rows = [tuple(row) for row in rows]

                    # Do not overwrite rows if this migration is re-run.
                    placeholders = ",".join(["%s"] * len(columns))
                    quoted = ",".join(f'"{c}"' for c in columns)
                    sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                    cur.executemany(sql, rows)
                    total += len(rows)
                print(f"Migrated {total} rows from legacy SQLite to PostgreSQL.")
    finally:
        pg.close()
        sqlite.close()


if __name__ == "__main__":
    main()
