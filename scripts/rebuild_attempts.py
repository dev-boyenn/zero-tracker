from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.attempt_tracker import AttemptTracker
from config import DB_PATH
from app.database import Database


def main() -> None:
    db = Database(DB_PATH)
    try:
        db.execute("DELETE FROM attempt_events")
        db.execute("DELETE FROM attempt_beds")
        db.execute("DELETE FROM attempts")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('attempt_events','attempt_beds','attempts')")

        tracker = AttemptTracker(db)
        rows = db.query_all(
            """
            SELECT id, chat_message, clock_time
            FROM raw_log_events
            WHERE is_chat = 1
            ORDER BY id ASC
            """
        )
        for row in rows:
            chat = row["chat_message"]
            if chat is None:
                continue
            tracker.handle_chat_event(
                event_id=int(row["id"]),
                chat_message=str(chat),
                clock_time=str(row["clock_time"]) if row["clock_time"] is not None else None,
            )

        total = db.query_one("SELECT COUNT(*) AS n FROM attempts")
        print(f"Rebuilt attempts: {int(total['n']) if total else 0}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
