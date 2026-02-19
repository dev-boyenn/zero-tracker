from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import time
from typing import Any

from config import MAJOR_DAMAGE_THRESHOLD
from scripts.parse_command_storage import (
    bedrock_by_node,
    dominant_node_from_storage,
    rotation_from_storage,
    run_metrics_from_storage,
)
from .metrics import (
    clear_runtime_atum_seed,
    is_mpk_full_random_override_enabled,
    rotate_mpk_seed_for_target_key,
    select_next_mpk_target,
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
        self.state_active_world_key = "mpk.active_world_name"
        self.last_seen_exit_event_id = 0
        self.world_create_re = re.compile(
            r'^Creating "(?P<world>.+)"(?: with seed "(?P<seed>[-\d]+)")?\.\.\.$'
        )
        self.world_load_re = re.compile(r"^Attempting event world load at (?P<world>.+)$")
        self.world_save_chunks_re = re.compile(r"Saving chunks for level 'ServerLevel\[(?P<world>.+?)\]'/")
        self.state_inworld_re = re.compile(r"^StateOutput State: inworld(?:,|$)")
        self.pending_world_name_for_seed_rotation: str | None = None
        self.pending_world_seed_for_seed_rotation: str | None = None
        self.last_rotated_world_name = self.db.get_state("mpk.seed_rotate.last_world", "") or ""
        self.active_world_name = self.db.get_state(self.state_active_world_key, "") or ""

    def handle_chat_event(self, event_id: int, chat_message: str, clock_time: str | None) -> None:
        # MPK ingestion is driven by world-exit log lines, not chat.
        return

    def handle_log_event(self, event_id: int, parsed: ParsedLogLine) -> None:
        body = (parsed.body or "").strip()
        transition_world = self._world_name_from_transition_line(body)
        # Language-agnostic ingest trigger: when a new world starts loading,
        # ingest the previous active world first.
        if transition_world and self.active_world_name and transition_world != self.active_world_name:
            self._ingest_latest_world(event_id=event_id, clock_time=parsed.clock_time)
        self._update_active_world_from_log(parsed)
        self._handle_seed_rotation_on_run_start(parsed)
        if event_id <= self.last_seen_exit_event_id:
            return
        if not body or not self._is_world_exit_line(body):
            return
        self.last_seen_exit_event_id = event_id
        self._ingest_latest_world(event_id=event_id, clock_time=parsed.clock_time)

    def _handle_seed_rotation_on_run_start(self, parsed: ParsedLogLine) -> None:
        body = (parsed.body or "").strip()
        if not body:
            return
        create_match = self.world_create_re.match(body)
        if create_match is not None:
            self.pending_world_name_for_seed_rotation = str(create_match.group("world"))
            seed_group = create_match.group("seed")
            self.pending_world_seed_for_seed_rotation = str(seed_group) if seed_group is not None else None
            return
        load_match = self.world_load_re.match(body)
        if load_match is not None:
            self.pending_world_name_for_seed_rotation = str(load_match.group("world"))
            # Seed is not present in this log line. Keep any captured seed if available.
            return
        if not self._is_seed_rotation_trigger_line(body):
            return
        world_name = self.pending_world_name_for_seed_rotation or self.active_world_name
        if not world_name:
            return
        current_world_seed = self.pending_world_seed_for_seed_rotation or ""
        self.pending_world_name_for_seed_rotation = None
        self.pending_world_seed_for_seed_rotation = None
        if world_name == self.last_rotated_world_name:
            return
        # Snapshot what the player is currently practicing (the seed that just loaded),
        # then queue the next target/seed for the next reset.
        current_target_key = self.db.get_state("mpk.practice.target_key", "") or ""
        current_seed_value = current_world_seed or (self.db.get_state("mpk.practice.seed_value", "") or "")
        self.active_world_name = world_name
        self.db.set_state(self.state_active_world_key, world_name)
        self.db.set_state("mpk.practice.current_world_name", world_name)
        self.db.set_state("mpk.practice.current_target_key", current_target_key)
        self.db.set_state("mpk.practice.current_seed_value", current_seed_value)
        self.db.set_state(
            "mpk.practice.current_selection_reason",
            self.db.get_state("mpk.practice.next_selection_reason", "") or "",
        )
        self.db.set_state(
            "mpk.practice.current_selection_mode",
            self.db.get_state("mpk.practice.next_selection_mode", "") or "",
        )
        self.db.set_state(
            "mpk.practice.current_requested_mode",
            self.db.get_state("mpk.practice.next_requested_mode", "") or "",
        )
        self.db.set_state(
            "mpk.practice.current_seed_mode",
            self.db.get_state("mpk.practice.next_seed_mode", "") or "set_seed",
        )
        if is_mpk_full_random_override_enabled(self.db):
            self.db.set_state("mpk.practice.target_key", "")
            self.db.set_state("mpk.practice.seed_value", "")
            self.db.set_state("mpk.practice.current_target_key", "")
            self.db.set_state("mpk.practice.current_seed_value", "")
            self.db.set_state("mpk.practice.current_selection_reason", "full_random_override")
            self.db.set_state("mpk.practice.current_selection_mode", "full_random_override")
            self.db.set_state("mpk.practice.current_requested_mode", "full_random_override")
            self.db.set_state("mpk.practice.current_seed_mode", "full_random")
            self.db.set_state("mpk.practice.next_selection_reason", "full_random_override")
            self.db.set_state("mpk.practice.next_selection_mode", "full_random_override")
            self.db.set_state("mpk.practice.next_requested_mode", "full_random_override")
            self.db.set_state("mpk.practice.next_seed_mode", "full_random")
            clear_state = clear_runtime_atum_seed(self.db)
            clear_error = clear_state.get("seed_clear_error")
            self.db.set_state("mpk.seed_rotate.last_error", str(clear_error or ""))
            self.last_rotated_world_name = world_name
            self.db.set_state("mpk.seed_rotate.last_world", world_name)
            return

        try:
            leniency_target = float(self.db.get_state("mpk.practice.leniency_target", "0") or "0")
        except ValueError:
            leniency_target = 0.0
        pick_result = select_next_mpk_target(self.db, leniency_target=leniency_target)
        pick = pick_result.get("pick")
        if pick is None:
            return
        candidate = pick.get("candidate") or {}
        target_key = str(candidate.get("target_key", "") or "")
        if not target_key.startswith("mpk|"):
            return
        seed_state = rotate_mpk_seed_for_target_key(self.db, target_key, advance=True)
        if seed_state.get("seed_apply_error"):
            self.db.set_state("mpk.seed_rotate.last_error", str(seed_state["seed_apply_error"]))
            return
        self.db.set_state("mpk.seed_rotate.last_error", "")
        self.db.set_state("mpk.practice.next_selection_reason", str(pick.get("selection_reason", "")))
        self.db.set_state("mpk.practice.next_selection_mode", str(pick.get("mode", "")))
        self.db.set_state("mpk.practice.next_requested_mode", str(pick.get("requested_mode", "")))
        self.db.set_state("mpk.practice.next_seed_mode", "set_seed")
        self.db.set_state("mpk.practice.next_mode_coverage_percent", str(pick.get("coverage_percent", 0.0)))
        self.db.set_state("mpk.practice.next_mode_qualified_targets", str(pick.get("qualified_targets", 0)))
        self.db.set_state("mpk.practice.next_mode_total_targets", str(pick.get("total_targets", 0)))
        self.db.set_state(
            "mpk.practice.next_mode_min_samples",
            str(pick.get("min_samples_per_target", 0)),
        )
        self.last_rotated_world_name = world_name
        self.db.set_state("mpk.seed_rotate.last_world", world_name)
        selected_seed = seed_state.get("selected_seed")
        if selected_seed is not None:
            self.db.set_state("mpk.seed_rotate.last_seed", str(selected_seed))

    def _update_active_world_from_log(self, parsed: ParsedLogLine) -> None:
        body = (parsed.body or "").strip()
        if not body:
            return
        save_match = self.world_save_chunks_re.search(body)
        if save_match is None:
            return
        world_name = str(save_match.group("world") or "").strip()
        if not world_name:
            return
        if world_name != self.active_world_name:
            self.active_world_name = world_name
            self.db.set_state(self.state_active_world_key, world_name)

    def _set_ingest_diag(self, *, reason: str, world_name: str = "", detail: str = "") -> None:
        self.db.set_state("mpk.ingest.last_reason", reason)
        self.db.set_state("mpk.ingest.last_world", world_name)
        self.db.set_state("mpk.ingest.last_detail", detail)

    def _is_world_exit_line(self, body: str) -> bool:
        # Language-agnostic / forced markers only.
        if body == "Stopping!":
            return True
        if body.startswith("StateOutput State: waiting"):
            return True
        return False

    def _world_name_from_transition_line(self, body: str) -> str | None:
        if not body:
            return None
        match = self.world_load_re.match(body)
        if match is not None:
            return str(match.group("world"))
        match = self.world_create_re.match(body)
        if match is not None:
            return str(match.group("world"))
        return None

    def _is_seed_rotation_trigger_line(self, body: str) -> bool:
        # Do not rely on localized vanilla/system lines like "joined the game".
        # These lines are emitted by mods and are stable in English.
        if body.startswith("Loaded StandardSettings on World Join"):
            return True
        if self.state_inworld_re.match(body) is not None:
            return True
        return False

    def _find_latest_world(self) -> Path | None:
        if not self.saves_dir.exists():
            return None
        worlds = [p for p in self.saves_dir.iterdir() if p.is_dir()]
        if not worlds:
            return None
        return max(worlds, key=lambda p: p.stat().st_mtime)

    def _find_world_for_ingest(self) -> Path | None:
        active = (self.active_world_name or "").strip()
        if active:
            candidate = self.saves_dir / active
            if candidate.exists() and candidate.is_dir():
                return candidate
        return self._find_latest_world()

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

    def _metrics_look_uninitialized(self, metrics: dict[str, Any]) -> bool:
        run_start_gt = int(metrics.get("run_start_gt", 0) or 0)
        run_end_gt = int(metrics.get("run_end_gt", 0) or 0)
        sample_count = int(metrics.get("sample_count", 0) or 0)
        end_entry_logged = bool(metrics.get("end_entry_logged", False))
        damage_events_count = int(metrics.get("damage_events_count", 0) or 0)
        beds_exploded = int(metrics.get("beds_exploded", 0) or 0)
        anchors_exploded_est = int(metrics.get("anchors_exploded_est", 0) or 0)
        return (
            run_start_gt <= 0
            and run_end_gt <= 0
            and sample_count <= 0
            and not end_entry_logged
            and damage_events_count <= 0
            and beds_exploded <= 0
            and anchors_exploded_est <= 0
        )

    def _ingest_latest_world(self, *, event_id: int, clock_time: str | None) -> None:
        world = self._find_world_for_ingest()
        if world is None:
            self._set_ingest_diag(reason="no_world")
            return
        world_name = world.name
        if world_name == (self.db.get_state(self.state_last_world_key, "") or ""):
            self._set_ingest_diag(reason="duplicate_world", world_name=world_name)
            return
        existing = self.db.query_one(
            "SELECT id FROM attempts WHERE attempt_source = 'mpk' AND world_name = ? LIMIT 1",
            (world_name,),
        )
        if existing is not None:
            self.db.set_state(self.state_last_world_key, world_name)
            self._set_ingest_diag(reason="already_inserted", world_name=world_name)
            return

        storage_path = self._find_storage_file(world / "data")
        if storage_path is None:
            self._set_ingest_diag(reason="no_storage", world_name=world_name)
            return
        if not self._wait_for_storage(storage_path):
            self._set_ingest_diag(reason="storage_not_ready", world_name=world_name)
            return

        node, _ = dominant_node_from_storage(storage_path, window_ticks=self.window_ticks)
        rotation = rotation_from_storage(storage_path, window_ticks=self.window_ticks)
        metrics = run_metrics_from_storage(storage_path)
        if self._metrics_look_uninitialized(metrics):
            retry_deadline = time.time() + 12.0
            while time.time() < retry_deadline:
                time.sleep(0.4)
                refreshed = run_metrics_from_storage(storage_path)
                if not self._metrics_look_uninitialized(refreshed):
                    metrics = refreshed
                    break
        if self._metrics_look_uninitialized(metrics):
            self._set_ingest_diag(reason="uninitialized_storage_snapshot", world_name=world_name)
            return
        bedrock = bedrock_by_node(world, radius=self.bedrock_radius)
        tower_height = bedrock.get(node) if node is not None else None
        tower_name = self._tower_name_from_height(tower_height)
        zero_type = self._zero_type_from_node(node, rotation)

        dragon_died = bool(metrics.get("dragon_died", False))
        flyaway_detected = bool(metrics.get("flyaway_detected", False))
        flyaway_gt = int(metrics.get("flyaway_detected_gt", 0) or 0)
        flyaway_dragon_y_raw = metrics.get("flyaway_dragon_y", None)
        flyaway_dragon_y = (
            int(flyaway_dragon_y_raw) if flyaway_dragon_y_raw is not None else None
        )
        flyaway_node = str(metrics.get("flyaway_node", "") or "")
        flyaway_crystals_alive = int(metrics.get("flyaway_crystals_alive", -1) or -1)
        flyaway_broke_crystal = flyaway_detected and (flyaway_crystals_alive >= 0 and flyaway_crystals_alive < 10)
        if dragon_died:
            status = "success"
            fail_reason = None
        elif flyaway_broke_crystal:
            status = "fail"
            fail_reason = "broke_crystal"
        elif flyaway_detected:
            status = "flyaway"
            fail_reason = "flyaway"
        else:
            status = "fail"
            fail_reason = "dragon_not_killed"

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
            self._set_ingest_diag(
                reason="min_end_ticks_not_met",
                world_name=world_name,
                detail=f"end_ticks={end_ticks}, min={self.MIN_END_TICKS_FOR_ATTEMPT}",
            )
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
        explosives_base_count = int(metrics.get("explosives_base_count", 0) or 0)
        explosives_plus_one_count = int(metrics.get("explosives_plus_one_count", 0) or 0)
        bows_shot = int(metrics.get("bows_shot", 0) or 0)
        crossbows_shot = int(metrics.get("crossbows_shot", 0) or 0)
        damage_events_count = int(metrics.get("damage_events_count", 0) or 0)
        explosive_standing_y_raw = metrics.get("explosive_standing_y", None)
        explosive_standing_y = int(explosive_standing_y_raw) if explosive_standing_y_raw is not None else None
        o_level = None
        if bool(metrics.get("end_entry_logged", False)):
            top_y = int(metrics.get("end_entry_top_y", -1) or -1)
            if top_y >= 0:
                o_level = top_y
        attempt_seed_mode = (self.db.get_state("mpk.practice.current_seed_mode", "") or "").strip().lower()
        if attempt_seed_mode not in {"full_random", "set_seed"}:
            attempt_seed_mode = "full_random" if is_mpk_full_random_override_enabled(self.db) else "set_seed"

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
                beds_exploded,
                anchors_exploded,
                bow_shots,
                crossbow_shots,
                major_damage_total,
                major_hit_count,
                setup_damage_total,
                setup_hit_count,
                max_damage_single_bed,
                attempt_source,
                attempt_seed_mode,
                o_level,
                flyaway_detected,
                flyaway_gt,
                flyaway_dragon_y,
                flyaway_node,
                flyaway_crystals_alive,
                world_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 'mpk', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                explosives_base_count if explosives_base_count > 0 else None,
                explosives_plus_one_count if explosives_plus_one_count > 0 else None,
                total_damage,
                damage_events_count,
                beds_exploded,
                anchors_exploded_est,
                bows_shot,
                crossbows_shot,
                major_damage_total,
                major_hit_count,
                max_damage_single,
                attempt_seed_mode,
                o_level,
                1 if flyaway_detected else 0,
                flyaway_gt,
                flyaway_dragon_y,
                flyaway_node if flyaway_node else None,
                flyaway_crystals_alive if flyaway_crystals_alive >= 0 else None,
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
        self._set_ingest_diag(reason="inserted", world_name=world_name)
