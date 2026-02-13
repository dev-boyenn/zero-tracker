from __future__ import annotations

import re
from datetime import UTC, datetime

from config import MAJOR_DAMAGE_THRESHOLD
from .database import Database
from .log_parser import ParsedLogLine

FIRST_BED_RE = re.compile(r"^(?P<seconds>\d+(?:\.\d+)?)s 1st Bed Placed$")
DAMAGE_RE = re.compile(r"^Damage:\s*(?P<damage>\d+)$")
BLOCKS_RE = re.compile(r"^(?P<blocks>\d+(?:\.\d+)?) Blocks$")
EXPLOSIVES_RE = re.compile(r"^Explosives:\s*(?P<used>\d+)(?:\+(?P<left>\d+))?$")
TIME_RE = re.compile(r"^Time:\s*(?P<seconds>\d+(?:\.\d+)?)s$")
TOWER_RE = re.compile(r"^Tower:\s*(?P<tower>.+?)(?:\s+\((?P<code>\d+)\))?$")
TYPE_RE = re.compile(r"^Type:\s*(?P<zero_type>.+)$")
HEIGHT_RE = re.compile(r"^Standing Height:\s*(?P<height>\d+)$")
ZDASH_TOWER_RE = re.compile(r"^\[ZDASH\]\s*Tower:\s*(?P<tower>.+)$")
ZDASH_TYPE_RE = re.compile(r"^\[ZDASH\]\s*Type:\s*(?P<zero_type>.+)$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AttemptTracker:
    def __init__(self, db: Database, require_pending_context: bool = True) -> None:
        self.db = db
        self.require_pending_context = require_pending_context
        self.current_attempt_id: int | None = None
        self.current_status: str | None = None
        self.current_bed_index = 0
        self.pending_tower_name: str | None = None
        self.pending_tower_code: str | None = None
        self.pending_zero_type: str | None = None
        self.pending_context_event_id: int | None = None
        self.pending_context_ttl_events = 300
        self._bootstrap_in_progress_attempt()

    def _bootstrap_in_progress_attempt(self) -> None:
        row = self.db.query_one(
            """
            SELECT id
            FROM attempts
            WHERE status = 'in_progress'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        if row is None:
            return
        attempt_id = int(row["id"])
        bed_row = self.db.query_one(
            "SELECT COALESCE(MAX(bed_index), -1) AS max_bed FROM attempt_beds WHERE attempt_id = ?",
            (attempt_id,),
        )
        self.current_attempt_id = attempt_id
        self.current_status = "in_progress"
        self.current_bed_index = int(bed_row["max_bed"]) + 1 if bed_row else 0

    def handle_chat_event(self, event_id: int, chat_message: str, clock_time: str | None) -> None:
        message = chat_message.strip()
        lower_message = message.lower()

        zdash_tower_match = ZDASH_TOWER_RE.match(message)
        if zdash_tower_match:
            self._finalize_open_attempt_if_needed(
                reason="new_attempt_started", clock_time=clock_time, event_id=event_id
            )
            tower_text = zdash_tower_match.group("tower").strip()
            prefixed_match = TOWER_RE.match(f"Tower: {tower_text}")
            if prefixed_match:
                self.pending_tower_name = prefixed_match.group("tower").strip()
                self.pending_tower_code = prefixed_match.group("code")
            else:
                self.pending_tower_name = tower_text
                self.pending_tower_code = None
            self.pending_context_event_id = event_id
            return

        zdash_type_match = ZDASH_TYPE_RE.match(message)
        if zdash_type_match:
            self._finalize_open_attempt_if_needed(
                reason="new_attempt_started", clock_time=clock_time, event_id=event_id
            )
            self.pending_zero_type = zdash_type_match.group("zero_type").strip()
            self.pending_context_event_id = event_id
            return

        first_bed_match = FIRST_BED_RE.match(message)
        if first_bed_match:
            self._hydrate_pending_from_recent_zdash(event_id=event_id)
            # Only register attempts while on the practice map where fresh
            # ZDASH context is available.
            if self.require_pending_context and not self._has_valid_pending_context(event_id):
                return
            self._finalize_open_attempt_if_needed(
                reason="new_attempt_started", clock_time=clock_time, event_id=event_id
            )
            seconds = float(first_bed_match.group("seconds"))
            self._start_attempt(event_id=event_id, first_bed_seconds=seconds, clock_time=clock_time)
            self._record_attempt_event(event_id, "first_bed_placed", value_num=seconds)
            return

        if self.current_attempt_id is None:
            return

        if message == "" and self.current_status in {"success", "fail"}:
            self._clear_current()
            return

        damage_match = DAMAGE_RE.match(message)
        if damage_match:
            damage = int(damage_match.group("damage"))
            is_major = damage >= MAJOR_DAMAGE_THRESHOLD
            damage_kind = "major" if is_major else "setup"
            self._record_damage(
                event_id=event_id,
                damage=damage,
                is_major=is_major,
                damage_kind=damage_kind,
            )
            return

        blocks_match = BLOCKS_RE.match(message)
        if blocks_match:
            blocks = float(blocks_match.group("blocks"))
            self._record_attempt_event(event_id, "distance_blocks", value_num=blocks)
            return

        if "dragon killed!" in lower_message:
            self._mark_success(event_id=event_id, clock_time=clock_time)
            self._record_attempt_event(event_id, "dragon_killed")
            return

        if "crystal destroyed" in lower_message:
            self._record_attempt_event(event_id, "crystal_destroyed")
            return

        explosives_match = EXPLOSIVES_RE.match(message)
        if explosives_match:
            used = int(explosives_match.group("used"))
            left_raw = explosives_match.group("left")
            left = int(left_raw) if left_raw is not None else None
            self._update_attempt_fields(explosives_used=used, explosives_left=left)
            self._record_attempt_event(event_id, "explosives", value_text=message)
            return

        run_time_match = TIME_RE.match(message)
        if run_time_match:
            seconds = float(run_time_match.group("seconds"))
            self._update_attempt_fields(success_time_seconds=seconds)
            self._record_attempt_event(event_id, "run_time", value_num=seconds)
            return

        tower_match = TOWER_RE.match(message)
        if tower_match:
            tower_name = tower_match.group("tower").strip()
            tower_code = tower_match.group("code")
            self._update_attempt_fields(tower_name=tower_name, tower_code=tower_code)
            self._record_attempt_event(event_id, "tower", value_text=message)
            return

        type_match = TYPE_RE.match(message)
        if type_match:
            zero_type = type_match.group("zero_type").strip()
            self._update_attempt_fields(zero_type=zero_type)
            self._record_attempt_event(event_id, "zero_type", value_text=zero_type)
            return

        height_match = HEIGHT_RE.match(message)
        if height_match:
            standing_height = int(height_match.group("height"))
            self._update_attempt_fields(standing_height=standing_height)
            self._record_attempt_event(event_id, "standing_height", value_num=standing_height)
            return

    def _start_attempt(self, event_id: int, first_bed_seconds: float, clock_time: str | None) -> None:
        now = self._event_ingested_at_utc(event_id) or utc_now()
        attempt_id = self.db.execute(
            """
            INSERT INTO attempts (
                started_event_id,
                started_at_utc,
                started_clock,
                status,
                first_bed_seconds,
                tower_name,
                tower_code,
                zero_type,
                created_at
            )
            VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                now,
                clock_time,
                first_bed_seconds,
                self.pending_tower_name,
                self.pending_tower_code,
                self.pending_zero_type,
                now,
            ),
        )
        self.current_attempt_id = attempt_id
        self.current_status = "in_progress"
        self.current_bed_index = 0
        # Consume pending context so it does not leak into unrelated future attempts.
        self.pending_tower_name = None
        self.pending_tower_code = None
        self.pending_zero_type = None
        self.pending_context_event_id = None

    def handle_log_event(self, event_id: int, parsed: ParsedLogLine) -> None:
        body = (parsed.body or "").strip()
        if not body:
            return
        lower = body.lower()
        left_practice = (
            "stateoutput state: title" in lower
            or "stateoutput state: waiting" in lower
            or "disconnecting from server" in lower
            or lower == "stopping!"
        )
        if left_practice:
            self.pending_tower_name = None
            self.pending_tower_code = None
            self.pending_zero_type = None
            self.pending_context_event_id = None
            self._finalize_open_attempt_if_needed(
                reason="left_practice_map",
                clock_time=parsed.clock_time,
                event_id=event_id,
            )

    def _hydrate_pending_from_recent_zdash(self, event_id: int) -> None:
        if self.pending_tower_name is not None and self.pending_zero_type is not None:
            return
        rows = self.db.query_all(
            """
            SELECT chat_message
            FROM raw_log_events
            WHERE is_chat = 1
              AND id < ?
              AND chat_message LIKE '[ZDASH] %'
            ORDER BY id DESC
            LIMIT 40
            """,
            (event_id,),
        )
        for row in rows:
            message = (row["chat_message"] or "").strip()
            if self.pending_tower_name is None:
                m_tower = ZDASH_TOWER_RE.match(message)
                if m_tower:
                    tower_text = m_tower.group("tower").strip()
                    prefixed_match = TOWER_RE.match(f"Tower: {tower_text}")
                    if prefixed_match:
                        self.pending_tower_name = prefixed_match.group("tower").strip()
                        self.pending_tower_code = prefixed_match.group("code")
                    else:
                        self.pending_tower_name = tower_text
                        self.pending_tower_code = None
            if self.pending_zero_type is None:
                m_type = ZDASH_TYPE_RE.match(message)
                if m_type:
                    self.pending_zero_type = m_type.group("zero_type").strip()
            if self.pending_tower_name is not None and self.pending_zero_type is not None:
                self.pending_context_event_id = event_id
                return

    def _has_valid_pending_context(self, event_id: int) -> bool:
        if self.pending_tower_name is None or self.pending_zero_type is None:
            return False
        anchor = self.pending_context_event_id
        if anchor is None:
            return False
        return (event_id - anchor) <= self.pending_context_ttl_events

    def _record_damage(self, event_id: int, damage: int, is_major: bool, damage_kind: str) -> None:
        if self.current_attempt_id is None:
            return
        now = utc_now()
        self.db.execute(
            """
            INSERT INTO attempt_beds (
                attempt_id,
                event_id,
                bed_index,
                damage,
                damage_kind,
                is_major,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.current_attempt_id,
                event_id,
                self.current_bed_index,
                damage,
                damage_kind,
                1 if is_major else 0,
                now,
            ),
        )
        self.db.execute(
            """
            UPDATE attempts
            SET total_damage = total_damage + ?,
                bed_count = bed_count + 1,
                major_damage_total = major_damage_total + ?,
                major_hit_count = major_hit_count + ?,
                setup_damage_total = setup_damage_total + ?,
                setup_hit_count = setup_hit_count + ?,
                max_damage_single_bed = CASE
                    WHEN ? > max_damage_single_bed THEN ?
                    ELSE max_damage_single_bed
                END
            WHERE id = ?
            """,
            (
                damage,
                damage if is_major else 0,
                1 if is_major else 0,
                0 if is_major else damage,
                0 if is_major else 1,
                damage,
                damage,
                self.current_attempt_id,
            ),
        )
        self._record_attempt_event(event_id, f"damage_{damage_kind}", value_num=damage)
        self.current_bed_index += 1

    def _mark_success(self, event_id: int, clock_time: str | None) -> None:
        if self.current_attempt_id is None:
            return
        now = self._event_ingested_at_utc(event_id) or utc_now()
        self.db.execute(
            """
            UPDATE attempts
            SET status = 'success',
                ended_at_utc = ?,
                ended_clock = ?
            WHERE id = ?
            """,
            (now, clock_time, self.current_attempt_id),
        )
        self.current_status = "success"
        self._record_attempt_event(event_id, "status_success")

    def _mark_fail(self, reason: str, clock_time: str | None, event_id: int | None = None) -> None:
        if self.current_attempt_id is None:
            return
        now = self._event_ingested_at_utc(event_id) if event_id is not None else None
        if now is None:
            now = utc_now()
        self.db.execute(
            """
            UPDATE attempts
            SET status = 'fail',
                fail_reason = ?,
                ended_at_utc = ?,
                ended_clock = ?
            WHERE id = ?
            """,
            (reason, now, clock_time, self.current_attempt_id),
        )
        self.current_status = "fail"
        self._clear_current()

    def _finalize_open_attempt_if_needed(
        self,
        reason: str,
        clock_time: str | None,
        event_id: int | None = None,
    ) -> None:
        if self.current_attempt_id is None:
            return
        if self.current_status == "in_progress":
            self._mark_fail(reason=reason, clock_time=clock_time, event_id=event_id)
            return
        self._clear_current()

    def _event_ingested_at_utc(self, event_id: int) -> str | None:
        row = self.db.query_one(
            "SELECT ingested_at_utc FROM raw_log_events WHERE id = ?",
            (event_id,),
        )
        if row is None:
            return None
        value = row["ingested_at_utc"]
        return str(value) if value is not None else None

    def _update_attempt_fields(self, **fields: object) -> None:
        if self.current_attempt_id is None or not fields:
            return
        allowed_columns = {
            "success_time_seconds",
            "tower_name",
            "tower_code",
            "zero_type",
            "standing_height",
            "explosives_used",
            "explosives_left",
        }
        updates = [(k, v) for k, v in fields.items() if k in allowed_columns]
        if not updates:
            return
        set_clause = ", ".join(f"{column} = ?" for column, _ in updates)
        params = [value for _, value in updates]
        params.append(self.current_attempt_id)
        self.db.execute(f"UPDATE attempts SET {set_clause} WHERE id = ?", params)

    def _record_attempt_event(
        self,
        event_id: int,
        event_type: str,
        value_text: str | None = None,
        value_num: float | int | None = None,
    ) -> None:
        if self.current_attempt_id is None:
            return
        self.db.execute(
            """
            INSERT INTO attempt_events (
                attempt_id,
                event_id,
                event_type,
                value_text,
                value_num,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (self.current_attempt_id, event_id, event_type, value_text, value_num, utc_now()),
        )

    def _clear_current(self) -> None:
        self.current_attempt_id = None
        self.current_status = None
        self.current_bed_index = 0
