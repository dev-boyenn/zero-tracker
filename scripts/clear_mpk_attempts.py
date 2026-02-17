from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Database
from config import DB_PATH


def main() -> int:
    db = Database(DB_PATH)
    try:
        before_row = db.query_one(
            "SELECT COUNT(*) AS n FROM attempts WHERE COALESCE(attempt_source, 'practice') = 'mpk'"
        )
        before = int(before_row["n"]) if before_row is not None else 0

        db.execute("DELETE FROM attempts WHERE COALESCE(attempt_source, 'practice') = 'mpk'")
        db.execute("DELETE FROM ingest_state WHERE key = 'mpk.last_ingested_world'")
        db.execute("DELETE FROM ingest_state WHERE key LIKE 'mpk.seed_rotate.%'")
        db.execute(
            """
            DELETE FROM ingest_state
            WHERE key LIKE 'mpk.practice.%'
              AND key <> 'mpk.practice.leniency_target'
            """
        )

        after_row = db.query_one(
            "SELECT COUNT(*) AS n FROM attempts WHERE COALESCE(attempt_source, 'practice') = 'mpk'"
        )
        after = int(after_row["n"]) if after_row is not None else 0
        deleted = before - after

        print(f"DB: {DB_PATH}")
        print(f"Deleted MPK attempts: {deleted}")
        print(f"Remaining MPK attempts: {after}")
        print("Reset state keys: mpk.last_ingested_world, mpk.seed_rotate.*, mpk.practice.* (except leniency_target)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
