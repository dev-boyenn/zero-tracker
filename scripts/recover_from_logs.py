from __future__ import annotations

import argparse
import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.attempt_tracker import AttemptTracker
from config import DB_PATH, LOG_PATH
from app.database import Database
from app.log_parser import parse_log_line

DATED_LOG_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-\d+\.log(?:\.gz)?$")


def _iter_lines(path: Path) -> Iterable[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\r\n")
    else:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\r\n")


def _file_sort_key(path: Path) -> tuple[datetime, str]:
    m = DATED_LOG_RE.match(path.name)
    if m:
        day = datetime.strptime(m.group("date"), "%Y-%m-%d")
        return (day, path.name)
    return (datetime.fromtimestamp(path.stat().st_mtime), path.name)


def _file_log_date(path: Path) -> datetime.date:
    m = DATED_LOG_RE.match(path.name)
    if m:
        return datetime.strptime(m.group("date"), "%Y-%m-%d").date()
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def _event_utc(log_date: datetime.date, clock_time: str | None) -> str:
    if clock_time:
        try:
            t = datetime.strptime(clock_time, "%H:%M:%S").time()
            local_dt = datetime.combine(log_date, t).astimezone()
            return local_dt.astimezone(UTC).isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.now(UTC).isoformat(timespec="seconds")


def _discover_files(logs_dir: Path, cutoff_local: datetime) -> list[Path]:
    files: list[Path] = []
    cutoff_date = cutoff_local.date()
    for p in logs_dir.iterdir():
        if not p.is_file():
            continue
        if p.name == "latest.log":
            if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff_local:
                files.append(p)
            continue
        if p.name.endswith(".log.gz"):
            if _file_log_date(p) >= cutoff_date:
                files.append(p)
    files.sort(key=_file_sort_key)
    return files


def _reset_tables(db: Database) -> None:
    db.execute("DELETE FROM attempt_events")
    db.execute("DELETE FROM attempt_beds")
    db.execute("DELETE FROM attempts")
    db.execute("DELETE FROM raw_log_events")
    db.execute("DELETE FROM ingest_state")
    db.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('raw_log_events','attempt_events','attempt_beds','attempts')"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recover dashboard DB from Minecraft logs. "
            "By default scans last 3 days and stops at first [ZDASH] marker."
        )
    )
    parser.add_argument(
        "--logs-dir",
        default=str(LOG_PATH.parent),
        help="Path to .minecraft/logs directory (default inferred from ZERO_DASH_LOG_PATH).",
    )
    parser.add_argument("--db-path", default=str(DB_PATH), help="SQLite DB output path.")
    parser.add_argument("--days", type=int, default=3, help="How many recent days of logs to scan.")
    parser.add_argument(
        "--start-on-zdash",
        action="store_true",
        default=True,
        help="Skip lines until first [ZDASH] chat marker in chronological order, then ingest from there (default on).",
    )
    parser.add_argument(
        "--no-start-on-zdash",
        dest="start_on_zdash",
        action="store_false",
        help="Do not wait for first [ZDASH] marker before ingesting.",
    )
    parser.add_argument(
        "--stop-on-zdash",
        action="store_true",
        default=False,
        help="Stop import at first [ZDASH] chat marker in chronological order (default off).",
    )
    parser.add_argument(
        "--no-stop-on-zdash",
        dest="stop_on_zdash",
        action="store_false",
        help="Do not stop at [ZDASH] marker.",
    )
    parser.add_argument(
        "--legacy-no-context",
        action="store_true",
        default=True,
        help="Allow pre-ZDASH attempts (do not require ZDASH context for attempt start). Default on.",
    )
    parser.add_argument(
        "--no-legacy-no-context",
        dest="legacy_no_context",
        action="store_false",
        help="Require ZDASH context for attempt start.",
    )
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir)
    db_path = Path(args.db_path)
    if not logs_dir.exists():
        print(f"Logs directory not found: {logs_dir}", file=sys.stderr)
        return 2

    cutoff_local = datetime.now() - timedelta(days=max(args.days, 0))
    files = _discover_files(logs_dir, cutoff_local)
    if not files:
        print(f"No logs found in {logs_dir} newer than {cutoff_local.isoformat(timespec='seconds')}")
        return 0

    db = Database(db_path)
    try:
        _reset_tables(db)
        tracker = AttemptTracker(db, require_pending_context=not args.legacy_no_context)

        stopped_at: str | None = None
        started_at: str | None = None
        started = not args.start_on_zdash
        raw_count = 0
        line_count = 0
        for path in files:
            log_date = _file_log_date(path)
            offset = 0
            for line in _iter_lines(path):
                line_count += 1
                parsed = parse_log_line(line)
                is_zdash = parsed.is_chat and (parsed.chat_message or "").startswith("[ZDASH]")
                if not started:
                    if is_zdash:
                        started = True
                        started_at = f"{path.name}:{offset}"
                    else:
                        offset += 1
                        continue
                if args.stop_on_zdash and is_zdash:
                    stopped_at = f"{path.name}:{offset}"
                    break

                ingested_at = _event_utc(log_date, parsed.clock_time)
                event_id = db.execute(
                    """
                    INSERT INTO raw_log_events (
                        ingested_at_utc, clock_time, thread_name, level, source,
                        is_chat, chat_message, raw_line, file_offset
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ingested_at,
                        parsed.clock_time,
                        parsed.thread_name,
                        parsed.level,
                        parsed.source,
                        1 if parsed.is_chat else 0,
                        parsed.chat_message,
                        parsed.raw_line,
                        offset,
                    ),
                )
                raw_count += 1
                if parsed.is_chat and parsed.chat_message is not None:
                    tracker.handle_chat_event(event_id, parsed.chat_message, parsed.clock_time)
                tracker.handle_log_event(event_id, parsed)
                offset += 1
            if stopped_at is not None:
                break

        attempts = db.query_one("SELECT COUNT(*) AS n FROM attempts")
        finished = db.query_one("SELECT COUNT(*) AS n FROM attempts WHERE status IN ('success','fail')")
        print(f"Scanned files: {len(files)}")
        print(f"Scanned lines: {line_count}")
        print(f"Inserted raw events: {raw_count}")
        print(f"Attempts total: {int(attempts['n']) if attempts else 0}")
        print(f"Attempts finished: {int(finished['n']) if finished else 0}")
        if stopped_at:
            print(f"Stopped at first [ZDASH] marker: {stopped_at}")
        if args.start_on_zdash:
            if started_at:
                print(f"Started ingest at first [ZDASH] marker: {started_at}")
            else:
                print("No [ZDASH] marker found; nothing ingested.")
        print(f"DB: {db_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
