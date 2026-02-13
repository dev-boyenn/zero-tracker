from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .attempt_tracker import AttemptTracker
from .database import Database
from .log_parser import parse_log_line

STATE_FILE_IDENTITY = "log_reader.file_identity"
STATE_FILE_POSITION = "log_reader.file_position"
STATE_LAST_HEARTBEAT = "log_reader.last_heartbeat_utc"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LogWatcher(threading.Thread):
    def __init__(self, log_path: Path, poll_seconds: float, db: Database, tracker: AttemptTracker) -> None:
        super().__init__(daemon=True, name="zero-cycle-log-watcher")
        self.log_path = log_path
        self.poll_seconds = poll_seconds
        self.db = db
        self.tracker = tracker
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        position = int(self.db.get_state(STATE_FILE_POSITION, "0") or "0")
        identity = self.db.get_state(STATE_FILE_IDENTITY, "")

        while not self.stop_event.is_set():
            if not self.log_path.exists():
                self.db.set_state(STATE_LAST_HEARTBEAT, utc_now())
                time.sleep(self.poll_seconds)
                continue

            stat = self.log_path.stat()
            # st_ctime changes on many systems whenever the file is modified,
            # which would incorrectly reset the read position on every append.
            # Device+inode is stable across appends and still changes on rotation/recreate.
            current_identity = f"{stat.st_dev}:{stat.st_ino}"
            same_file = (
                identity == current_identity
                or identity.startswith(f"{current_identity}:")
            )

            if not same_file:
                identity = current_identity
                position = 0
                self.db.set_state(STATE_FILE_IDENTITY, identity)

            if stat.st_size < position:
                position = 0

            with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                while True:
                    line_start = handle.tell()
                    line = handle.readline()
                    if line == "":
                        break
                    position = handle.tell()
                    self._ingest_line(raw_line=line.rstrip("\r\n"), file_offset=line_start)

            self.db.set_state(STATE_FILE_POSITION, str(position))
            self.db.set_state(STATE_LAST_HEARTBEAT, utc_now())
            time.sleep(self.poll_seconds)

    def _ingest_line(self, raw_line: str, file_offset: int) -> None:
        parsed = parse_log_line(raw_line)
        event_id = self.db.execute(
            """
            INSERT INTO raw_log_events (
                ingested_at_utc,
                clock_time,
                thread_name,
                level,
                source,
                is_chat,
                chat_message,
                raw_line,
                file_offset
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                parsed.clock_time,
                parsed.thread_name,
                parsed.level,
                parsed.source,
                1 if parsed.is_chat else 0,
                parsed.chat_message,
                parsed.raw_line,
                file_offset,
            ),
        )
        if parsed.is_chat and parsed.chat_message is not None:
            self.tracker.handle_chat_event(
                event_id=event_id,
                chat_message=parsed.chat_message,
                clock_time=parsed.clock_time,
            )
        else:
            self.tracker.handle_log_event(event_id=event_id, parsed=parsed)
