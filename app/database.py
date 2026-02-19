from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS raw_log_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingested_at_utc TEXT NOT NULL,
            clock_time TEXT,
            thread_name TEXT,
            level TEXT,
            source TEXT,
            is_chat INTEGER NOT NULL,
            chat_message TEXT,
            raw_line TEXT NOT NULL,
            file_offset INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_raw_ingested
            ON raw_log_events (ingested_at_utc);

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_event_id INTEGER,
            started_at_utc TEXT NOT NULL,
            started_clock TEXT,
            ended_at_utc TEXT,
            ended_clock TEXT,
            status TEXT NOT NULL DEFAULT 'in_progress',
            fail_reason TEXT,
            first_bed_seconds REAL,
            success_time_seconds REAL,
            tower_name TEXT,
            tower_code TEXT,
            zero_type TEXT,
            standing_height INTEGER,
            explosives_used INTEGER,
            explosives_left INTEGER,
            total_damage INTEGER NOT NULL DEFAULT 0,
            bed_count INTEGER NOT NULL DEFAULT 0,
            beds_exploded INTEGER NOT NULL DEFAULT 0,
            anchors_exploded INTEGER NOT NULL DEFAULT 0,
            bow_shots INTEGER NOT NULL DEFAULT 0,
            crossbow_shots INTEGER NOT NULL DEFAULT 0,
            major_damage_total INTEGER NOT NULL DEFAULT 0,
            major_hit_count INTEGER NOT NULL DEFAULT 0,
            setup_damage_total INTEGER NOT NULL DEFAULT 0,
            setup_hit_count INTEGER NOT NULL DEFAULT 0,
            max_damage_single_bed INTEGER NOT NULL DEFAULT 0,
            attempt_source TEXT NOT NULL DEFAULT 'practice',
            attempt_seed_mode TEXT NOT NULL DEFAULT 'set_seed',
            o_level INTEGER,
            flyaway_detected INTEGER NOT NULL DEFAULT 0,
            flyaway_gt INTEGER NOT NULL DEFAULT 0,
            flyaway_dragon_y INTEGER,
            flyaway_node TEXT,
            flyaway_crystals_alive INTEGER,
            world_name TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(started_event_id) REFERENCES raw_log_events(id)
        );

        CREATE INDEX IF NOT EXISTS idx_attempts_status
            ON attempts (status);
        CREATE INDEX IF NOT EXISTS idx_attempts_started
            ON attempts (started_at_utc);
        CREATE INDEX IF NOT EXISTS idx_attempts_status_id
            ON attempts (status, id);
        CREATE INDEX IF NOT EXISTS idx_attempts_scope
            ON attempts (id, status, tower_name, zero_type, started_at_utc);
        CREATE TABLE IF NOT EXISTS attempt_beds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            bed_index INTEGER NOT NULL,
            damage INTEGER NOT NULL,
            damage_kind TEXT NOT NULL DEFAULT 'unknown',
            is_major INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(attempt_id) REFERENCES attempts(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES raw_log_events(id)
        );

        CREATE INDEX IF NOT EXISTS idx_attempt_beds_attempt
            ON attempt_beds (attempt_id);
        CREATE INDEX IF NOT EXISTS idx_attempt_beds_index
            ON attempt_beds (bed_index);
        CREATE INDEX IF NOT EXISTS idx_attempt_beds_attempt_major
            ON attempt_beds (attempt_id, is_major, id);

        CREATE TABLE IF NOT EXISTS attempt_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            value_text TEXT,
            value_num REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(attempt_id) REFERENCES attempts(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES raw_log_events(id)
        );

        CREATE INDEX IF NOT EXISTS idx_attempt_events_attempt
            ON attempt_events (attempt_id);
        CREATE INDEX IF NOT EXISTS idx_attempt_events_type
            ON attempt_events (event_type);

        CREATE TABLE IF NOT EXISTS ingest_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with self._lock:
            self._conn.executescript(schema)
            self._migrate_schema()
            self._conn.commit()

    def _has_column(self, table: str, column: str) -> bool:
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        columns = [str(row[1]) for row in cur.fetchall()]
        return column in columns

    def _migrate_schema(self) -> None:
        migrations: list[tuple[str, str, str]] = [
            ("attempts", "major_damage_total", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "major_hit_count", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "setup_damage_total", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "setup_hit_count", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "attempt_source", "TEXT NOT NULL DEFAULT 'practice'"),
            ("attempts", "attempt_seed_mode", "TEXT NOT NULL DEFAULT 'set_seed'"),
            ("attempts", "o_level", "INTEGER"),
            ("attempts", "flyaway_detected", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "flyaway_gt", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "flyaway_dragon_y", "INTEGER"),
            ("attempts", "flyaway_node", "TEXT"),
            ("attempts", "flyaway_crystals_alive", "INTEGER"),
            ("attempts", "world_name", "TEXT"),
            ("attempts", "beds_exploded", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "anchors_exploded", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "bow_shots", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "crossbow_shots", "INTEGER NOT NULL DEFAULT 0"),
            ("attempt_beds", "damage_kind", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("attempt_beds", "is_major", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, column, definition in migrations:
            if not self._has_column(table, column):
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Backfill old rows when new fields were introduced.
        self._conn.execute(
            """
            UPDATE attempt_beds
            SET is_major = CASE WHEN damage >= 15 THEN 1 ELSE 0 END
            WHERE damage_kind = 'unknown'
            """
        )
        self._conn.execute(
            """
            UPDATE attempt_beds
            SET damage_kind = CASE
                WHEN is_major = 1 THEN 'major'
                ELSE 'setup'
            END
            WHERE damage_kind = 'unknown'
            """
        )
        self._conn.execute(
            """
            UPDATE attempts
            SET
                major_damage_total = COALESCE(
                    (
                        SELECT SUM(damage)
                        FROM attempt_beds b
                        WHERE b.attempt_id = attempts.id AND b.is_major = 1
                    ),
                    0
                ),
                major_hit_count = COALESCE(
                    (
                        SELECT COUNT(*)
                        FROM attempt_beds b
                        WHERE b.attempt_id = attempts.id AND b.is_major = 1
                    ),
                    0
                ),
                setup_damage_total = COALESCE(
                    (
                        SELECT SUM(damage)
                        FROM attempt_beds b
                        WHERE b.attempt_id = attempts.id AND b.is_major = 0
                    ),
                    0
                ),
                setup_hit_count = COALESCE(
                    (
                        SELECT COUNT(*)
                        FROM attempt_beds b
                        WHERE b.attempt_id = attempts.id AND b.is_major = 0
                    ),
                    0
                )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_attempt_beds_kind
            ON attempt_beds (damage_kind)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_attempts_source_started
            ON attempts (attempt_source, started_at_utc)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_attempts_source_seedmode_started
            ON attempts (attempt_source, attempt_seed_mode, started_at_utc)
            """
        )
        self._conn.execute(
            """
            UPDATE attempts
            SET attempt_seed_mode = 'set_seed'
            WHERE attempt_seed_mode IS NULL
               OR TRIM(COALESCE(attempt_seed_mode, '')) = ''
               OR attempt_seed_mode NOT IN ('set_seed', 'full_random')
            """
        )
        # Keep MPK tower names aligned with practice-map naming.
        self._conn.execute(
            """
            UPDATE attempts
            SET tower_name = CASE
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 76 THEN 'Small Boy'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 79 THEN 'Small Cage'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 82 THEN 'Tall Cage'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 85 THEN 'M-85'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 88 THEN 'M-88'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 91 THEN 'M-91'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 94 THEN 'T-94'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 97 THEN 'T-97'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 100 THEN 'T-100'
                WHEN COALESCE(CAST(tower_code AS INTEGER), standing_height) = 103 THEN 'Tall Boy'
                ELSE tower_name
            END
            WHERE COALESCE(attempt_source, 'practice') = 'mpk'
            """
        )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return int(cur.lastrowid)

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return list(cur.fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.fetchone()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM ingest_state WHERE key = ?", (key,))
        if row is None:
            return default
        return str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO ingest_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
