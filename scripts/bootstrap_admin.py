"""Guarded, manually invoked bootstrap/reset for the Dental doctor administrator.

This utility is intentionally NOT wired to a GitHub Actions workflow. Run it only
from an approved operator environment with production access, after reviewing the
target DATABASE_URL and the intended administrator account.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email",
        default="drpranali.jadhav890@gmail.com",
        help="Administrator email to create or reset (default: clinic administrator).",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Explicitly run Alembic migrations to head before provisioning.",
    )
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help="Explicitly replace the password and promote an existing target account to admin.",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Required acknowledgement before this script can write to the configured database.",
    )
    args = parser.parse_args()

    if not args.confirm_production:
        parser.error("refusing to write without --confirm-production")
    if not args.migrate:
        parser.error("refusing to provision before migrations; pass --migrate explicitly")

    database_url = os.getenv("DATABASE_URL", "").strip()
    password = os.getenv("DENTAL_BOOTSTRAP_ADMIN_PASSWORD", "")
    clinic_id = os.getenv("DENTAL_MASTER_CLINIC_ID", "").strip()
    email = args.email.strip().lower()

    if not database_url:
        parser.error("DATABASE_URL is required")
    if not password:
        parser.error("DENTAL_BOOTSTRAP_ADMIN_PASSWORD must be provided through the environment")
    if len(password) < 12:
        parser.error("DENTAL_BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
    if not clinic_id:
        parser.error("DENTAL_MASTER_CLINIC_ID is required; refusing to guess clinic ownership")
    if "@" not in email or len(email) > 320:
        parser.error("a valid administrator email is required")

    # Normalize Render's legacy scheme before the store and Alembic read it.
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    os.environ["DATABASE_URL"] = database_url
    os.environ["DENTAL_AUTO_MIGRATE"] = "true"
    os.environ["ALEMBIC_CONFIG"] = str(ROOT / "alembic.ini")

    # Import after environment validation; DentalStore runs Alembic on construction
    # only because the explicit --migrate flag enabled DENTAL_AUTO_MIGRATE.
    from universal_intelligence_engine.dental_auth import hash_password
    from universal_intelligence_engine.dental_store import get_dental_store

    store = get_dental_store()
    existing = store.doctor_by_email(email)

    if existing and not args.reset_existing:
        print(
            "Migration completed. The target account already exists; no account changes made. "
            "Re-run only with --reset-existing if an intentional password reset is approved."
        )
        return 2

    password_hash = hash_password(password)
    if existing:
        placeholder = "%s" if store.database_url else "?"
        rows = store._execute(
            "UPDATE dental_doctors SET password_hash="
            + placeholder
            + ", role='admin', active=true WHERE id="
            + placeholder
            + " RETURNING id",
            (password_hash, existing["id"]),
        )
        if not rows:
            raise RuntimeError("Administrator password reset did not update an account")
        print("Administrator password reset completed; secret value was not displayed.")
        return 0

    import secrets

    store.create_doctor(
        {
            "id": secrets.token_hex(16),
            "email": email,
            "password_hash": password_hash,
            "role": "admin",
            "clinic_id": clinic_id,
        }
    )
    print("Administrator account created; secret value was not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
