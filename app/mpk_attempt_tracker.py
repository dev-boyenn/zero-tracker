from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import time
from typing import Any

from config import MAJOR_DAMAGE_THRESHOLD
from scripts.parse_command_storage import (
    bedrock_by_node,
    dominant_node_from_storage,
    rotation_from_storage,
    run_metrics_from_storage,
)

from .database import Database
from .log_parser import ParsedLogLine


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MpkAttemptTracker:
    TOWER_NAME_BY_HEIGHT = {
        76: "Small Boy",
        79: "Small Cage",
        82: "Tall Cage",
        85: "M-85",
        88: "M-88",
        91: "M-91",
        94: "T-94",
        97: "T-97",
        100: "T-100",
        103: "Tall Boy",
    }
    MIN_END_TICKS_FOR_ATTEMPT = 100

    def __init__(
        self,
        db: Database,
        saves_dir: Path,
        *,
        window_ticks: int = 600,
        bedrock_radius: int = 4,
        storage_wait_seconds: float = 35.0,
    ) -> None:
        self.db = db
        self.saves_dir = saves_dir
        self.window_ticks = window_ticks
        self.bedrock_radius = bedrock_radius
        self.storage_wait_seconds = storage_wait_seconds
        self.state_last_world_key = "mpk.last_ingested_world"
        self.last_seen_exit_event_id = 0

    def handle_chat_event(self, event_id: int, chat_message: str, clock_time: str | None) -> None:
        # MPK ingestion is driven by world-exit log lines, not chat.
        return

    def handle_log_event(self, event_id: int, parsed: ParsedLogLine) -> None:
        if event_id <= self.last_seen_exit_event_id:
            return
        body = (parsed.body or "").strip()
        if not body or not self._is_world_exit_line(body):
            return
        self.last_seen_exit_event_id = event_id
        self._ingest_latest_world(event_id=event_id, clock_time=parsed.clock_time)

    def _is_world_exit_line(self, body: str) -> bool:
        lower = body.lower()
        return (
            "disconnecting from server" in lower
            or "left the game" in lower
            or lower == "stopping!"
        )

    def _find_latest_world(self) -> Path | None:
        if not self.saves_dir.exists():
            return None
        worlds = [p for p in self.saves_dir.iterdir() if p.is_dir()]
        if not worlds:
            return None
        return max(worlds, key=lambda p: p.stat().st_mtime)

    def _find_storage_file(self, data_dir: Path) -> Path | None:
        preferred = data_dir / "command_storage_zdash.dat"
        if preferred.exists():
            return preferred
        candidates = sorted(
            data_dir.glob("command_storage_*.dat"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _wait_for_storage(self, path: Path) -> bool:
        deadline = datetime.now(UTC) + timedelta(seconds=self.storage_wait_seconds)
        last_size = -1
        stable_since: datetime | None = None
        while datetime.now(UTC) < deadline:
            if path.exists() and path.stat().st_size > 0:
                size = path.stat().st_size
                if size == last_size:
                    if stable_since is None:
                        stable_since = datetime.now(UTC)
                    elif (datetime.now(UTC) - stable_since).total_seconds() >= 1.5:
                        return True
                else:
                    stable_since = None
                last_size = size
            else:
                stable_since = None
                last_size = -1
            time.sleep(0.25)
        return False

    def _event_ingested_at_utc(self, event_id: int) -> str | None:
        row = self.db.query_one("SELECT ingested_at_utc FROM raw_log_events WHERE id = ?", (event_id,))
        if row is None:
            return None
        value = row["ingested_at_utc"]
        return str(value) if value is not None else None

    def _iso_minus_seconds(self, ended_at_utc: str, seconds: float) -> str:
        try:
            ended_dt = datetime.fromisoformat(ended_at_utc)
        except Exception:
            return ended_at_utc
        started_dt = ended_dt - timedelta(seconds=max(0.0, float(seconds)))
        return started_dt.isoformat(timespec="seconds")

    def _zero_type_from_node(self, node: str | None, rotation: str) -> str:
        if not node:
            return "Unknown"
        side = "Front" if node.startswith("front_") else "Back" if node.startswith("back_") else "Unknown"
        shape = "Straight" if node.endswith("_straight") else "Diagonal" if node.endswith("_diag") else "Unknown"
        rot = rotation.upper() if rotation in {"cw", "ccw"} else "Unknown"
        if side == "Unknown" or shape == "Unknown":
            return "Unknown"
        return f"{side} {shape} {rot}"

    def _tower_name_from_height(self, height: int | None) -> str:
        if height is None or height < 0:
            return "Unknown"
        return self.TOWER_NAME_BY_HEIGHT.get(height, f"T-{height}")

    def _explosive_event_count(self, mapped_events: list[dict[str, Any]]) -> int:
        explosive_sources = {
            "bed",
            "anchor",
            "mixed",
            "mixed_explosive",
            "mixed_bed_other",
            "mixed_anchor_other",
        }
        return sum(1 for ev in mapped_events if str(ev.get("source", "other")) in explosive_sources)

    def _ingest_latest_world(self, *, event_id: int, clock_time: str | None) -> None:
        world = self._find_latest_world()
        if world is None:
            return
        world_name = world.name
        if world_name == (self.db.get_state(self.state_last_world_key, "") or ""):
            return
        existing = self.db.query_one(
            "SELECT id FROM attempts WHERE attempt_source = 'mpk' AND world_name = ? LIMIT 1",
            (world_name,),
        )
        if existing is not None:
            self.db.set_state(self.state_last_world_key, world_name)
            return

        storage_path = self._find_storage_file(world / "data")
        if storage_path is None:
            return
        if not self._wait_for_storage(storage_path):
            return

        node, _ = dominant_node_from_storage(storage_path, window_ticks=self.window_ticks)
        rotation = rotation_from_storage(storage_path, window_ticks=self.window_ticks)
        metrics = run_metrics_from_storage(storage_path)
        bedrock = bedrock_by_node(world, radius=self.bedrock_radius)
        tower_height = bedrock.get(node) if node is not None else None
        tower_name = self._tower_name_from_height(tower_height)
        zero_type = self._zero_type_from_node(node, rotation)

        dragon_died = bool(metrics.get("dragon_died", False))
        status = "success" if dragon_died else "fail"
        fail_reason = None if dragon_died else "dragon_not_killed"

        start_gt = int(metrics.get("run_start_gt", 0) or 0)
        died_gt = int(metrics.get("dragon_died_gt", 0) or 0)
        end_gt = int(metrics.get("run_end_gt", 0) or 0)
        last_sample_gt = int(metrics.get("last_sample_gt", 0) or 0)
        gt_candidates = [gt for gt in (died_gt, end_gt, last_sample_gt) if gt > 0]
        final_gt = max(gt_candidates) if gt_candidates else 0
        end_entry_logged = bool(metrics.get("end_entry_logged", False))
        end_entry_gt = int(metrics.get("end_entry_gt", 0) or 0)
        end_ticks = (final_gt - end_entry_gt) if end_entry_logged and final_gt > end_entry_gt else 0
        if end_ticks < self.MIN_END_TICKS_FOR_ATTEMPT:
            # Ignore short End visits; caller asked to only count real attempts.
            return
        duration_seconds = max(0.0, (final_gt - start_gt) / 20.0) if final_gt > start_gt else 0.0

        ended_at_utc = self._event_ingested_at_utc(event_id) or utc_now()
        started_at_utc = self._iso_minus_seconds(ended_at_utc, duration_seconds) if duration_seconds > 0 else ended_at_utc

        bed_damage = float(metrics.get("bed_damage_est", 0.0) or 0.0)
        anchor_damage = float(metrics.get("anchor_damage_est", 0.0) or 0.0)
        other_damage = float(metrics.get("other_damage_est", 0.0) or 0.0)
        total_damage = int(round(bed_damage + anchor_damage + other_damage))
        major_damage_total = int(round(bed_damage + anchor_damage))

        mapped_damage_events = metrics.get("mapped_damage_events", [])
        if not isinstance(mapped_damage_events, list):
            mapped_damage_events = []
        major_hit_count = self._explosive_event_count(mapped_damage_events)
        max_damage_single = max(
            (int(ev.get("hp_diff_scaled", 0) or 0) for ev in mapped_damage_events),
            default=0,
        )

        beds_exploded = int(metrics.get("beds_exploded", 0) or 0)
        anchors_exploded_est = int(metrics.get("anchors_exploded_est", 0) or 0)
        damage_events_count = int(metrics.get("damage_events_count", 0) or 0)
        explosive_standing_y_raw = metrics.get("explosive_standing_y", None)
        explosive_standing_y = int(explosive_standing_y_raw) if explosive_standing_y_raw is not None else None
        o_level = None
        if bool(metrics.get("end_entry_logged", False)):
            top_y = int(metrics.get("end_entry_top_y", -1) or -1)
            if top_y >= 0:
                o_level = top_y

        attempt_id = self.db.execute(
            """
            INSERT INTO attempts (
                started_event_id,
                started_at_utc,
                started_clock,
                ended_at_utc,
                ended_clock,
                status,
                fail_reason,
                success_time_seconds,
                tower_name,
                tower_code,
                zero_type,
                standing_height,
                explosives_used,
                explosives_left,
                total_damage,
                bed_count,
                major_damage_total,
                major_hit_count,
                setup_damage_total,
                setup_hit_count,
                max_damage_single_bed,
                attempt_source,
                o_level,
                world_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 'mpk', ?, ?, ?)
            """,
            (
                event_id,
                started_at_utc,
                clock_time,
                ended_at_utc,
                clock_time,
                status,
                fail_reason,
                duration_seconds if status == "success" and duration_seconds > 0 else None,
                tower_name,
                str(tower_height) if tower_height is not None else None,
                zero_type,
                explosive_standing_y,
                beds_exploded if beds_exploded > 0 else None,
                anchors_exploded_est if anchors_exploded_est > 0 else None,
                total_damage,
                damage_events_count,
                major_damage_total,
                major_hit_count,
                max_damage_single,
                o_level,
                world_name,
                ended_at_utc,
            ),
        )

        bed_index = 0
        for ev in mapped_damage_events:
            source = str(ev.get("source", "other"))
            if source not in {"bed", "anchor", "mixed", "mixed_explosive", "mixed_bed_other", "mixed_anchor_other"}:
                continue
            damage = int(ev.get("hp_diff_scaled", 0) or 0)
            if damage <= 0:
                continue
            is_major = damage >= MAJOR_DAMAGE_THRESHOLD
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
                    attempt_id,
                    event_id,
                    bed_index,
                    damage,
                    "major" if is_major else "setup",
                    1 if is_major else 0,
                    ended_at_utc,
                ),
            )
            bed_index += 1

        self.db.set_state(self.state_last_world_key, world_name)
