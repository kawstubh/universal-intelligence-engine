from __future__ import annotations

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / ".test-dental.sqlite3"
os.environ["DENTAL_SQLITE_PATH"] = str(_TEST_DB)
os.environ["DENTAL_AUTO_MIGRATE"] = "true"
os.environ.pop("DATABASE_URL", None)
