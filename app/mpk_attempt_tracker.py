from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from config import MAJOR_DAMAGE_THRESHOLD, PROJECT_ROOT
from scripts.parse_command_storage import (
    bedrock_by_node,
    dominant_node_from_storage,
    rotation_from_storage,
    run_metrics_from_storage,
    world_seed_from_level_dat,
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
        self.state_retry_world_key = "mpk.ingest.retry_world_name"
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

    def _set_retry_world(self, world_name: str | None) -> None:
        value = (world_name or "").strip()
        self.db.set_state(self.state_retry_world_key, value)

    def _get_retry_world(self) -> str:
        return (self.db.get_state(self.state_retry_world_key, "") or "").strip()

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
        stronghold_samples_raw = metrics.get("stronghold_samples", [])
        if isinstance(stronghold_samples_raw, list) and len(stronghold_samples_raw) > 0:
            # Stronghold-only runs can be valid even when End-fight counters are absent.
            return False
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

    def _extract_stronghold_samples(self, metrics: dict[str, Any]) -> list[dict[str, int]]:
        stronghold_samples_raw = metrics.get("stronghold_samples", [])
        stronghold_samples: list[dict[str, int]] = []
        if not isinstance(stronghold_samples_raw, list):
            return stronghold_samples
        for item in stronghold_samples_raw:
            if not isinstance(item, dict):
                continue
            stronghold_samples.append(
                {
                    "gt": int(item.get("gt", 0) or 0),
                    "x": int(item.get("x", 0) or 0),
                    "y": int(item.get("y", 0) or 0),
                    "z": int(item.get("z", 0) or 0),
                    "dim": int(item.get("dim", 0) or 0),
                }
            )
        return stronghold_samples

    def _world_output_stem(self, world_name: str) -> str:
        match = re.search(r"#(\d+)", str(world_name))
        if match:
            return match.group(1)
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid else ch for ch in str(world_name))
        cleaned = cleaned.strip().strip(".")
        if not cleaned:
            cleaned = "world"
        return cleaned.replace(" ", "_")

    def _analyze_stronghold_world(self, *, world: Path, attempt_id: int) -> None:
        stronghold_maps_dir = PROJECT_ROOT / "data" / "stronghold_maps"
        stronghold_maps_dir.mkdir(parents=True, exist_ok=True)
        out_stem = self._world_output_stem(world.name)
        out_json = stronghold_maps_dir / f"{out_stem}.json"
        analyzer_path = PROJECT_ROOT / "scripts" / "analyze_stronghold_world.py"
        if not analyzer_path.exists():
            self.db.set_state("mpk.stronghold.last_error", f"Analyzer missing: {analyzer_path}")
            return

        cmd = [
            sys.executable,
            str(analyzer_path),
            str(world),
            "--out",
            str(out_json),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "").strip()
        if proc.stderr:
            stderr = proc.stderr.strip()
            output = f"{output}\n{stderr}".strip()
        self.db.set_state("mpk.stronghold.last_output", output[:5000] if output else "")
        if int(proc.returncode) != 0:
            self.db.set_state(
                "mpk.stronghold.last_error",
                f"analyze_stronghold_world failed ({proc.returncode}) for {world.name}",
            )
            return
        if not out_json.exists():
            self.db.set_state(
                "mpk.stronghold.last_error",
                f"analysis succeeded but output missing: {out_json}",
            )
            return

        try:
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception as exc:
            self.db.set_state("mpk.stronghold.last_error", f"Failed to parse {out_json.name}: {exc}")
            return

        starter = payload.get("starter", {}) if isinstance(payload, dict) else {}
        sample_stats = payload.get("sample_stats", {}) if isinstance(payload, dict) else {}
        optimal_nav = payload.get("optimal_nav", {}) if isinstance(payload, dict) else {}
        visits_raw = payload.get("visits", []) if isinstance(payload, dict) else []
        visits = visits_raw if isinstance(visits_raw, list) else []

        portal_entered = any(
            isinstance(v, dict) and str(v.get("room_type", "")) == "PortalRoom"
            for v in visits
        )
        nav_enter_gts: list[int] = []
        nav_exit_gts: list[int] = []
        for visit in visits:
            if not isinstance(visit, dict):
                continue
            enter_gt = int(visit.get("enter_gt", 0) or 0)
            exit_gt = int(visit.get("exit_gt", 0) or 0)
            if enter_gt > 0:
                nav_enter_gts.append(enter_gt)
            if exit_gt > 0:
                nav_exit_gts.append(exit_gt)
        nav_ticks_fallback = 0
        if nav_enter_gts and nav_exit_gts:
            nav_start_gt = min(nav_enter_gts)
            nav_end_gt = max(nav_exit_gts)
            if nav_end_gt > nav_start_gt:
                nav_ticks_fallback = int(nav_end_gt - nav_start_gt)
        nav_seconds_fallback = (float(nav_ticks_fallback) / 20.0) if nav_ticks_fallback > 0 else 0.0

        existing_nav = self.db.query_one(
            "SELECT stronghold_nav_ticks, stronghold_nav_seconds FROM attempts WHERE id = ?",
            (attempt_id,),
        )
        existing_nav_ticks = (
            int(existing_nav["stronghold_nav_ticks"] or 0)
            if existing_nav is not None and existing_nav["stronghold_nav_ticks"] is not None
            else 0
        )
        existing_nav_seconds = (
            float(existing_nav["stronghold_nav_seconds"] or 0.0)
            if existing_nav is not None and existing_nav["stronghold_nav_seconds"] is not None
            else 0.0
        )
        nav_ticks_final = existing_nav_ticks if existing_nav_ticks > 0 else nav_ticks_fallback
        nav_seconds_final = (
            existing_nav_seconds if existing_nav_seconds > 0 else nav_seconds_fallback
        )
        room_ticks: list[int] = []
        room_seconds: list[float] = []
        for visit in visits:
            if not isinstance(visit, dict):
                continue
            ticks = int(visit.get("duration_ticks", 0) or 0)
            seconds = float(visit.get("duration_seconds", 0.0) or 0.0)
            if ticks > 0:
                room_ticks.append(ticks)
            if seconds > 0:
                room_seconds.append(seconds)
        avg_room_ticks = (
            (sum(room_ticks) / float(len(room_ticks))) if room_ticks else None
        )
        avg_room_seconds = (
            (sum(room_seconds) / float(len(room_seconds))) if room_seconds else None
        )

        rooms_entered = int(sample_stats.get("mapped_rooms", 0) or 0)
        starter_ticks = int(starter.get("ticks", 0) or 0)
        starter_seconds = float(starter.get("seconds", 0.0) or 0.0)
        # Keep room-delta comparable to optimal-nav: count visited unique rooms
        # from the same starter room used by optimal-nav through first portal entry.
        comparable_rooms_entered = None
        try:
            starter_room_id = int(optimal_nav.get("starter_room_id", -1) or -1)
        except Exception:
            starter_room_id = -1
        if visits:
            starter_idx = 0
            if starter_room_id >= 0:
                for idx, visit in enumerate(visits):
                    if not isinstance(visit, dict):
                        continue
                    if int(visit.get("room_id", -1) or -1) == starter_room_id:
                        starter_idx = idx
                        break
            portal_idx = None
            for idx in range(starter_idx, len(visits)):
                visit = visits[idx]
                if not isinstance(visit, dict):
                    continue
                if str(visit.get("room_type", "")) == "PortalRoom":
                    portal_idx = idx
                    break
            end_idx = portal_idx + 1 if portal_idx is not None else len(visits)
            room_ids: set[int] = set()
            for visit in visits[starter_idx:end_idx]:
                if not isinstance(visit, dict):
                    continue
                room_id = int(visit.get("room_id", -1) or -1)
                if room_id >= 0:
                    room_ids.add(room_id)
            if room_ids:
                comparable_rooms_entered = len(room_ids)
        optimal_rooms = None
        optimal_edges = None
        room_delta = None
        if bool(optimal_nav.get("reachable", False)):
            optimal_rooms_raw = optimal_nav.get("min_rooms", None)
            optimal_edges_raw = optimal_nav.get("min_edges", None)
            if optimal_rooms_raw is not None:
                optimal_rooms = int(optimal_rooms_raw)
            if optimal_edges_raw is not None:
                optimal_edges = int(optimal_edges_raw)
            rooms_for_delta = (
                int(comparable_rooms_entered)
                if comparable_rooms_entered is not None
                else int(rooms_entered)
            )
            if optimal_rooms is not None and rooms_for_delta > 0:
                room_delta = int(rooms_for_delta - optimal_rooms)

        out_svg = out_json.with_suffix(".svg")
        json_path_str = str(out_json)
        svg_path_str = str(out_svg) if out_svg.exists() else None
        self.db.execute(
            """
            UPDATE attempts
            SET
                stronghold_nav_ticks = ?,
                stronghold_nav_seconds = ?,
                stronghold_rooms_entered = ?,
                stronghold_starter_ticks = ?,
                stronghold_starter_seconds = ?,
                stronghold_avg_room_ticks = ?,
                stronghold_avg_room_seconds = ?,
                stronghold_portal_room_entered = ?,
                stronghold_optimal_rooms = ?,
                stronghold_optimal_edges = ?,
                stronghold_room_delta = ?,
                stronghold_map_json_path = ?,
                stronghold_map_svg_path = ?
            WHERE id = ?
            """,
            (
                nav_ticks_final if nav_ticks_final > 0 else None,
                nav_seconds_final if nav_seconds_final > 0 else None,
                rooms_entered if rooms_entered > 0 else None,
                starter_ticks if starter_ticks > 0 else None,
                starter_seconds if starter_seconds > 0 else None,
                avg_room_ticks,
                avg_room_seconds,
                1 if portal_entered else 0,
                optimal_rooms,
                optimal_edges,
                room_delta,
                json_path_str,
                svg_path_str,
                attempt_id,
            ),
        )
        self.db.set_state("mpk.stronghold.last_error", "")

    def _backfill_existing_stronghold_attempt(
        self,
        *,
        attempt_id: int,
        world: Path,
        storage_path: Path,
        storage_fingerprint: str,
    ) -> bool:
        metrics = run_metrics_from_storage(storage_path)
        if self._metrics_look_uninitialized(metrics):
            retry_deadline = time.time() + 8.0
            while time.time() < retry_deadline:
                time.sleep(0.4)
                refreshed = run_metrics_from_storage(storage_path)
                if not self._metrics_look_uninitialized(refreshed):
                    metrics = refreshed
                    break
        stronghold_samples = self._extract_stronghold_samples(metrics)
        if not stronghold_samples:
            return False

        stronghold_eye_spy_gt = int(metrics.get("stronghold_eye_spy_gt", 0) or 0)
        stronghold_end_enter_gt = int(metrics.get("stronghold_end_enter_gt", 0) or 0)
        stronghold_nav_ticks = int(metrics.get("stronghold_nav_ticks", 0) or 0)
        stronghold_nav_seconds = float(metrics.get("stronghold_nav_seconds", 0.0) or 0.0)
        now_utc = utc_now()

        self.db.execute("DELETE FROM stronghold_samples WHERE attempt_id = ?", (attempt_id,))
        for sample_idx, sample in enumerate(stronghold_samples):
            self.db.execute(
                """
                INSERT INTO stronghold_samples (
                    attempt_id,
                    sample_index,
                    gt,
                    x,
                    y,
                    z,
                    dim,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    sample_idx,
                    int(sample.get("gt", 0) or 0),
                    int(sample.get("x", 0) or 0),
                    int(sample.get("y", 0) or 0),
                    int(sample.get("z", 0) or 0),
                    int(sample.get("dim", 0) or 0),
                    now_utc,
                ),
            )

        self.db.execute(
            """
            UPDATE attempts
            SET
                storage_fingerprint = ?,
                stronghold_sample_count = ?,
                stronghold_eye_spy_gt = ?,
                stronghold_end_enter_gt = ?,
                stronghold_nav_ticks = ?,
                stronghold_nav_seconds = ?
            WHERE id = ?
            """,
            (
                storage_fingerprint or None,
                len(stronghold_samples),
                stronghold_eye_spy_gt if stronghold_eye_spy_gt > 0 else None,
                stronghold_end_enter_gt if stronghold_end_enter_gt > 0 else None,
                stronghold_nav_ticks if stronghold_nav_ticks > 0 else None,
                stronghold_nav_seconds if stronghold_nav_seconds > 0 else None,
                attempt_id,
            ),
        )
        try:
            self._analyze_stronghold_world(world=world, attempt_id=attempt_id)
        except Exception as exc:
            self.db.set_state(
                "mpk.stronghold.last_error",
                f"Unexpected stronghold re-analyze failure for {world.name}: {exc}",
            )
        return True

    def _ingest_latest_world(self, *, event_id: int, clock_time: str | None) -> None:
        retry_world_name = self._get_retry_world()
        if retry_world_name:
            retry_world = self.saves_dir / retry_world_name
            if retry_world.exists() and retry_world.is_dir():
                active_world_name = (self.active_world_name or "").strip()
                prefer_retry = True
                # Do not let an old stuck retry world starve newer worlds forever.
                if active_world_name and active_world_name != retry_world_name:
                    active_world = self.saves_dir / active_world_name
                    if active_world.exists() and active_world.is_dir():
                        try:
                            prefer_retry = retry_world.stat().st_mtime >= active_world.stat().st_mtime
                        except Exception:
                            prefer_retry = False
                    else:
                        prefer_retry = False
                if prefer_retry:
                    world = retry_world
                else:
                    self._set_retry_world("")
                    world = self._find_world_for_ingest()
            else:
                self._set_retry_world("")
                world = self._find_world_for_ingest()
        else:
            world = self._find_world_for_ingest()
        if world is None:
            self._set_ingest_diag(reason="no_world")
            return
        world_name = world.name

        storage_path = self._find_storage_file(world / "data")
        if storage_path is None:
            self._set_retry_world(world_name)
            self._set_ingest_diag(reason="no_storage", world_name=world_name)
            return
        if not self._wait_for_storage(storage_path):
            self._set_retry_world(world_name)
            self._set_ingest_diag(reason="storage_not_ready", world_name=world_name)
            return
        try:
            stat = storage_path.stat()
            storage_fingerprint = f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        except Exception:
            storage_fingerprint = ""
        existing = self.db.query_one(
            """
            SELECT id, COALESCE(stronghold_sample_count, 0) AS stronghold_sample_count
            FROM attempts
            WHERE attempt_source = 'mpk'
              AND world_name = ?
              AND COALESCE(storage_fingerprint, '') = ?
            LIMIT 1
            """,
            (world_name, storage_fingerprint),
        )
        if existing is not None:
            existing_attempt_id = int(existing["id"])
            existing_stronghold_count = int(existing["stronghold_sample_count"] or 0)
            if existing_stronghold_count <= 0:
                if self._backfill_existing_stronghold_attempt(
                    attempt_id=existing_attempt_id,
                    world=world,
                    storage_path=storage_path,
                    storage_fingerprint=storage_fingerprint,
                ):
                    self._set_retry_world("")
                    self.db.set_state(self.state_last_world_key, world_name)
                    self._set_ingest_diag(
                        reason="rehydrated_existing_stronghold",
                        world_name=world_name,
                        detail=f"attempt_id={existing_attempt_id}, stronghold_samples>0",
                    )
                    return
            self.db.set_state(self.state_last_world_key, world_name)
            self._set_retry_world("")
            self._set_ingest_diag(
                reason="already_inserted",
                world_name=world_name,
                detail=f"fingerprint={storage_fingerprint}",
            )
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
            self._set_retry_world(world_name)
            self._set_ingest_diag(reason="uninitialized_storage_snapshot", world_name=world_name)
            return
        bedrock = bedrock_by_node(world, radius=self.bedrock_radius)
        world_seed = world_seed_from_level_dat(world)
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
        stronghold_samples = self._extract_stronghold_samples(metrics)
        if not stronghold_samples:
            # Storage sometimes lags a little behind world-exit; give stronghold
            # samples a short additional window before finalizing insertion.
            retry_deadline = time.time() + 6.0
            while time.time() < retry_deadline:
                time.sleep(0.3)
                refreshed = run_metrics_from_storage(storage_path)
                refreshed_samples = self._extract_stronghold_samples(refreshed)
                if refreshed_samples:
                    metrics = refreshed
                    stronghold_samples = refreshed_samples
                    break
        stronghold_sample_count = len(stronghold_samples)
        zero_attempt_eligible = 1 if end_ticks >= self.MIN_END_TICKS_FOR_ATTEMPT else 0
        if zero_attempt_eligible == 0 and stronghold_sample_count <= 0:
            # Skip worlds that are neither valid zero attempts nor stronghold-tracked attempts.
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
        start_white_beds = int(metrics.get("end_entry_start_white_beds", 0) or 0)
        start_anchors = int(metrics.get("end_entry_start_anchors", 0) or 0)
        start_bow = int(metrics.get("end_entry_start_bow", 0) or 0)
        start_crossbow = int(metrics.get("end_entry_start_crossbow", 0) or 0)
        damage_events_count = int(metrics.get("damage_events_count", 0) or 0)
        stronghold_eye_spy_gt = int(metrics.get("stronghold_eye_spy_gt", 0) or 0)
        stronghold_end_enter_gt = int(metrics.get("stronghold_end_enter_gt", 0) or 0)
        stronghold_nav_ticks = int(metrics.get("stronghold_nav_ticks", 0) or 0)
        stronghold_nav_seconds = float(metrics.get("stronghold_nav_seconds", 0.0) or 0.0)
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
                start_white_beds,
                start_anchors,
                start_bow,
                start_crossbow,
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
                world_seed,
                storage_fingerprint,
                zero_attempt_eligible,
                stronghold_eye_spy_gt,
                stronghold_end_enter_gt,
                stronghold_nav_ticks,
                stronghold_nav_seconds,
                stronghold_sample_count,
                stronghold_rooms_entered,
                stronghold_starter_ticks,
                stronghold_starter_seconds,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 'mpk', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id if event_id > 0 else None,
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
                start_white_beds,
                start_anchors,
                start_bow,
                start_crossbow,
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
                int(world_seed) if world_seed is not None else None,
                storage_fingerprint or None,
                zero_attempt_eligible,
                stronghold_eye_spy_gt if stronghold_eye_spy_gt > 0 else None,
                stronghold_end_enter_gt if stronghold_end_enter_gt > 0 else None,
                stronghold_nav_ticks if stronghold_nav_ticks > 0 else None,
                stronghold_nav_seconds if stronghold_nav_seconds > 0 else None,
                stronghold_sample_count if stronghold_sample_count > 0 else 0,
                None,
                None,
                None,
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

        for sample_idx, sample in enumerate(stronghold_samples):
            self.db.execute(
                """
                INSERT INTO stronghold_samples (
                    attempt_id,
                    sample_index,
                    gt,
                    x,
                    y,
                    z,
                    dim,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    sample_idx,
                    int(sample.get("gt", 0) or 0),
                    int(sample.get("x", 0) or 0),
                    int(sample.get("y", 0) or 0),
                    int(sample.get("z", 0) or 0),
                    int(sample.get("dim", 0) or 0),
                    ended_at_utc,
                ),
            )

        if stronghold_sample_count > 0:
            try:
                self._analyze_stronghold_world(world=world, attempt_id=attempt_id)
            except Exception as exc:
                self.db.set_state(
                    "mpk.stronghold.last_error",
                    f"Unexpected stronghold analyze failure for {world_name}: {exc}",
                )

        self.db.set_state(self.state_last_world_key, world_name)
        self._set_retry_world("")
        self._set_ingest_diag(
            reason="inserted" if zero_attempt_eligible else "inserted_stronghold_only",
            world_name=world_name,
            detail=f"end_ticks={end_ticks}, stronghold_samples={stronghold_sample_count}, zero_eligible={zero_attempt_eligible}",
        )
