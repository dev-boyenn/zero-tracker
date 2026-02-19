from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Database
from config import DB_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete exactly one MPK attempt by attempt ID."
    )
    parser.add_argument(
        "id",
        type=int,
        help="Attempt ID to delete (must exist and be MPK source).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    attempt_id = int(args.id)
    if attempt_id <= 0:
        print(f"Invalid attempt id: {attempt_id}. Must be > 0.")
        return 2

    db = Database(DB_PATH)
    try:
        row = db.query_one(
            """
            SELECT id, status, tower_name, zero_type, started_at_utc,
                   COALESCE(attempt_source, 'practice') AS attempt_source
            FROM attempts
            WHERE id = ?
            """,
            (attempt_id,),
        )
        if row is None:
            print(f"No attempt found with id={attempt_id}.")
            return 1

        source = str(row["attempt_source"] or "practice")
        if source != "mpk":
            print(
                f"Attempt id={attempt_id} is source='{source}', not 'mpk'. "
                "Refusing to delete."
            )
            return 1

        before = db.query_one("SELECT changes() AS n")
        _ = before  # keep lint happy for different sqlite builds
        db.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))
        changed_row = db.query_one("SELECT changes() AS n")
        changed = int(changed_row["n"]) if changed_row is not None else 0
        if changed != 1:
            print(
                f"Delete guard failed for id={attempt_id}. "
                f"Expected 1 row changed, got {changed}."
            )
            return 1

        print(f"DB: {DB_PATH}")
        print(
            "Deleted MPK attempt: "
            f"id={int(row['id'])}, status={row['status']}, "
            f"tower={row['tower_name']}, type={row['zero_type']}, "
            f"started_at={row['started_at_utc']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
