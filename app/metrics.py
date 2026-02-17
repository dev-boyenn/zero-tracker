from __future__ import annotations

import csv
from contextvars import ContextVar
import json
from pathlib import Path
import random
import re
from typing import Any

from config import (
    MPK_BACK_DIAG_JSON_PATH,
    MPK_BACK_DIAG_TSV_PATH,
    MPK_FRONT_DIAG_JSON_PATH,
    MPK_FRONT_DIAG_TSV_PATH,
    MPK_SEEDS_MAP_PATH,
)

from .database import Database

WINDOW_MIN_ID_CTX: ContextVar[int | None] = ContextVar("window_min_id", default=None)
WINDOW_START_UTC_CTX: ContextVar[str | None] = ContextVar("window_start_utc", default=None)
INCLUDE_STRAIGHT_CTX: ContextVar[bool] = ContextVar("include_straight", default=True)
ROTATION_FILTER_CTX: ContextVar[str] = ContextVar("rotation_filter", default="both")
ATTEMPT_SOURCE_CTX: ContextVar[str] = ContextVar("attempt_source_filter", default="mpk")
MPK_LENIENCY_TARGET_CTX: ContextVar[float] = ContextVar("mpk_leniency_target", default=0.0)

_MPK_EXPECTED_TOWERS = [
    "Small Boy",
    "Small Cage",
    "Tall Cage",
    "M-85",
    "M-88",
    "M-91",
    "T-94",
    "T-97",
    "T-100",
    "Tall Boy",
]
_MPK_TOWER_TO_SEEDMAP = {
    "Small Boy": "SMALL_BOY",
    "Small Cage": "SMALL_CAGE",
    "Tall Cage": "TALL_CAGE",
    "M-85": "M_85",
    "M-88": "M_88",
    "M-91": "M_91",
    "T-94": "T_94",
    "T-97": "T_97",
    "T-100": "T_100",
    "Tall Boy": "TALL_BOY",
}
_MPK_SEEDMAP_TO_TOWER = {v: k for k, v in _MPK_TOWER_TO_SEEDMAP.items()}
_MPK_SIDE_ORDER = {"Front": 0, "Back": 1}
_MPK_TOWER_ORDER = {name: idx for idx, name in enumerate(_MPK_EXPECTED_TOWERS)}
_MPK_MAP_KEY_RE = re.compile(r"^(Front|Back)\s+([A-Z0-9_]+)!(.+)$")
_MPK_LEVEL_SUFFIX_RE = re.compile(r"\s(-?\d+)$")
_MPK_FORCED_AIR_LEVELS = {49, 50, 51, 52}
_MPK_REQUIRED_SPAWN_FOR_HIGH_O = "BURIED_FLAT"
_MPK_MAX_O_LEVEL = 60
_HEATMAP_EXCLUDED_O_LEVELS = {49, 50, 51, 52, 53}
_MPK_HEATMAP_MIN_LEVEL = 48
_MPK_HEATMAP_MAX_LEVEL = 60
_MPK_MIN_SAMPLES_PER_TARGET = 2
_MPK_WEAK_MIN_STREAK_TO_SWAP = 3
_MPK_MODE_CURSOR_KEY = "mpk.practice.mode_cursor"
_MPK_RECENT_TARGETS_KEY = "mpk.practice.recent_targets"
_MPK_LAST_MODE_KEY = "mpk.practice.last_mode"
_MPK_WEAK_LOCK_TARGET_KEY = "mpk.practice.weak_lock_target_key"
_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY = "mpk.practice.weak_lock_anchor_attempt_id"
_MPK_LOCKED_TARGETS_KEY = "mpk.practice.locked_targets"
_MPK_SEED_MAP_CACHE_PATH: Path | None = None
_MPK_SEED_MAP_CACHE_MTIME: float | None = None
_MPK_SEED_MAP_CACHE: dict[tuple[str, str, int], list[int]] | None = None
_MPK_SEED_MAP_LEVELS: list[int] = []
_MPK_SEED_MAP_ERROR: str | None = None
_MPK_LENIENCY_CACHE_JSON_PATHS: tuple[Path, Path] | None = None
_MPK_LENIENCY_CACHE_JSON_MTIMES: tuple[float | None, float | None] | None = None
_MPK_LENIENCY_CACHE_TSV_PATHS: tuple[Path, Path] | None = None
_MPK_LENIENCY_CACHE_TSV_MTIMES: tuple[float | None, float | None] | None = None
_MPK_LENIENCY_CACHE: dict[tuple[str, str, int], float] | None = None
_MPK_LENIENCY_ERROR: str | None = None


def _load_mpk_seed_map() -> tuple[dict[tuple[str, str, int], list[int]], list[int], str | None]:
    global _MPK_SEED_MAP_CACHE_PATH
    global _MPK_SEED_MAP_CACHE_MTIME
    global _MPK_SEED_MAP_CACHE
    global _MPK_SEED_MAP_LEVELS
    global _MPK_SEED_MAP_ERROR

    path = MPK_SEEDS_MAP_PATH
    if not path.exists():
        return {}, [], f"Missing seeds map file: {path}"
    try:
        mtime = float(path.stat().st_mtime)
    except OSError:
        return {}, [], f"Cannot stat seeds map file: {path}"
    if (
        _MPK_SEED_MAP_CACHE is not None
        and _MPK_SEED_MAP_CACHE_PATH == path
        and _MPK_SEED_MAP_CACHE_MTIME == mtime
    ):
        return _MPK_SEED_MAP_CACHE, _MPK_SEED_MAP_LEVELS, _MPK_SEED_MAP_ERROR

    combos: dict[tuple[str, str, int], list[int]] = {}
    levels: set[int] = set()
    error: str | None = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_map = payload.get("_map", {})
        if not isinstance(raw_map, dict):
            error = f"Invalid seeds map payload (_map missing): {path}"
            raw_map = {}
        for raw_key, raw_seeds in raw_map.items():
            key_text = str(raw_key or "").strip()
            match = _MPK_MAP_KEY_RE.match(key_text)
            if not match:
                continue
            side, tower_key, spawn_text = match.groups()
            spawn_clean = str(spawn_text or "").strip()
            spawn_upper = spawn_clean.upper()
            if spawn_upper == "VOID":
                # In seedsMap, VOID corresponds to Open.
                spawn_kind = "VOID"
                o_level = 48
            else:
                level_match = _MPK_LEVEL_SUFFIX_RE.search(spawn_clean)
                if level_match is None:
                    continue
                spawn_kind = str(spawn_clean[: level_match.start()] or "").strip().upper()
                try:
                    o_level = int(level_match.group(1))
                except ValueError:
                    continue
            # Requested MPK filtering:
            # - O49..O52 are forced-air and should never be considered.
            # - For O > 48, only BURIED_FLAT seeds are valid for target injection.
            if o_level in _MPK_FORCED_AIR_LEVELS:
                continue
            if o_level > _MPK_MAX_O_LEVEL:
                continue
            if o_level > 48 and spawn_kind != _MPK_REQUIRED_SPAWN_FOR_HIGH_O:
                continue
            tower_name = _MPK_SEEDMAP_TO_TOWER.get(tower_key)
            if tower_name is None:
                continue
            if side not in {"Front", "Back"}:
                continue
            if not isinstance(raw_seeds, list):
                continue
            combo_key = (tower_name, side, o_level)
            if combo_key not in combos:
                combos[combo_key] = []
            for raw_seed in raw_seeds:
                try:
                    seed_value = int(raw_seed)
                except (TypeError, ValueError):
                    continue
                combos[combo_key].append(seed_value)
            if combos[combo_key]:
                levels.add(o_level)
    except Exception as exc:
        combos = {}
        levels = set()
        error = f"Failed reading seeds map: {exc}"

    _MPK_SEED_MAP_CACHE_PATH = path
    _MPK_SEED_MAP_CACHE_MTIME = mtime
    _MPK_SEED_MAP_CACHE = combos
    _MPK_SEED_MAP_LEVELS = sorted(levels)
    _MPK_SEED_MAP_ERROR = error
    return combos, _MPK_SEED_MAP_LEVELS, error


def _path_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def _normalize_leniency_target(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed != parsed:  # NaN
        return 0.0
    if parsed == float("inf"):
        return 9999.0
    if parsed == float("-inf"):
        return -9999.0
    return parsed


def _load_leniency_entries_from_json(path: Path, default_side: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    side = str(payload.get("side") or default_side).strip() or default_side
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        tower_name = str(raw.get("tower_name") or "").strip()
        if not tower_name:
            continue
        try:
            o_level = int(raw.get("o_level"))
            standing_height = int(raw.get("standing_height"))
            leniency = float(raw.get("leniency"))
        except (TypeError, ValueError):
            continue
        entries.append(
            {
                "tower_name": tower_name,
                "side": side,
                "o_level": o_level,
                "standing_height": standing_height,
                "leniency": leniency,
            }
        )
    return entries


def _parse_int_prefix(text: str) -> int | None:
    match = re.search(r"(\d+)", str(text or ""))
    if match is None:
        return None
    return int(match.group(1))


def _load_leniency_entries_from_tsv(path: Path, side: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows:
        return []
    header = [str(c or "").strip().strip('"') for c in rows[0]]
    level_cols: list[tuple[int, int]] = []
    for idx, name in enumerate(header):
        key = name.lower().strip()
        if key == "open":
            level_cols.append((48, idx))
            continue
        if key.startswith("o"):
            try:
                level_cols.append((int(key[1:]), idx))
            except ValueError:
                continue

    tower_by_height = {
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
    entries: list[dict[str, Any]] = []
    current_height: int | None = None
    for row in rows[1:]:
        if len(row) < 3:
            continue
        tower_cell = str(row[0] or "").strip()
        if tower_cell:
            current_height = _parse_int_prefix(tower_cell)
        if current_height is None:
            continue
        tower_name = tower_by_height.get(current_height)
        if tower_name is None:
            continue
        standing_height = _parse_int_prefix(row[1] if len(row) > 1 else "")
        if standing_height is None:
            continue
        for o_level, col_idx in level_cols:
            if col_idx >= len(row):
                continue
            raw_value = str(row[col_idx] or "").strip()
            if not raw_value:
                continue
            try:
                leniency = float(raw_value)
            except ValueError:
                continue
            entries.append(
                {
                    "tower_name": tower_name,
                    "side": side,
                    "o_level": int(o_level),
                    "standing_height": int(standing_height),
                    "leniency": leniency,
                }
            )
    return entries


def _load_mpk_leniency_lookup() -> tuple[dict[tuple[str, str, int], float], str | None]:
    global _MPK_LENIENCY_CACHE_JSON_PATHS
    global _MPK_LENIENCY_CACHE_JSON_MTIMES
    global _MPK_LENIENCY_CACHE_TSV_PATHS
    global _MPK_LENIENCY_CACHE_TSV_MTIMES
    global _MPK_LENIENCY_CACHE
    global _MPK_LENIENCY_ERROR

    json_paths = (MPK_FRONT_DIAG_JSON_PATH, MPK_BACK_DIAG_JSON_PATH)
    json_mtimes = tuple(_path_mtime(p) for p in json_paths)
    tsv_paths = (MPK_FRONT_DIAG_TSV_PATH, MPK_BACK_DIAG_TSV_PATH)
    tsv_mtimes = tuple(_path_mtime(p) for p in tsv_paths)
    if (
        _MPK_LENIENCY_CACHE is not None
        and _MPK_LENIENCY_CACHE_JSON_PATHS == json_paths
        and _MPK_LENIENCY_CACHE_JSON_MTIMES == json_mtimes
        and _MPK_LENIENCY_CACHE_TSV_PATHS == tsv_paths
        and _MPK_LENIENCY_CACHE_TSV_MTIMES == tsv_mtimes
    ):
        return _MPK_LENIENCY_CACHE, _MPK_LENIENCY_ERROR

    front_entries: list[dict[str, Any]] = []
    back_entries: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if MPK_FRONT_DIAG_JSON_PATH.exists() and MPK_BACK_DIAG_JSON_PATH.exists():
            front_entries = _load_leniency_entries_from_json(MPK_FRONT_DIAG_JSON_PATH, "Front")
            back_entries = _load_leniency_entries_from_json(MPK_BACK_DIAG_JSON_PATH, "Back")
        else:
            front_entries = _load_leniency_entries_from_tsv(MPK_FRONT_DIAG_TSV_PATH, "Front")
            back_entries = _load_leniency_entries_from_tsv(MPK_BACK_DIAG_TSV_PATH, "Back")
    except Exception as exc:
        errors.append(f"Failed reading leniency data: {exc}")

    lookup: dict[tuple[str, str, int], float] = {}
    for entry in front_entries + back_entries:
        try:
            standing_height = int(entry["standing_height"])
            if standing_height not in {94, 95}:
                continue
            key = (str(entry["tower_name"]), str(entry["side"]), int(entry["o_level"]))
            value = float(entry["leniency"])
        except (TypeError, ValueError, KeyError):
            continue
        current = lookup.get(key)
        if current is None or value > current:
            lookup[key] = value

    if not lookup and not errors:
        errors.append("No leniency rows found at standing heights 94/95.")

    _MPK_LENIENCY_CACHE_JSON_PATHS = json_paths
    _MPK_LENIENCY_CACHE_JSON_MTIMES = json_mtimes
    _MPK_LENIENCY_CACHE_TSV_PATHS = tsv_paths
    _MPK_LENIENCY_CACHE_TSV_MTIMES = tsv_mtimes
    _MPK_LENIENCY_CACHE = lookup
    _MPK_LENIENCY_ERROR = "; ".join(errors) if errors else None
    return lookup, _MPK_LENIENCY_ERROR


def _resolve_runtime_atum_json_path(db: Database) -> Path | None:
    saved = (db.get_state("setup.mpk_instance_path", "") or "").strip()
    if not saved:
        return None
    base = Path(saved)
    if base.name.lower() != ".minecraft":
        child = base / ".minecraft"
        base = child if child.exists() else base
    return base / "config" / "mcsr" / "atum.json"


def _write_mpk_seed_to_atum_json_for_path(path: Path, seed_value: int) -> str | None:
    try:
        if not path.exists():
            return f"Missing atum.json: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return f"Invalid atum.json payload: {path}"
        data["seed"] = str(seed_value)
        rendered = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
        path.write_text(rendered, encoding="utf-8")
        return None
    except Exception as exc:
        return f"Failed writing atum.json: {exc}"


def parse_mpk_target_key(target_key: str) -> tuple[str, str, int] | None:
    parts = str(target_key or "").split("|", 3)
    if len(parts) != 4 or parts[0] != "mpk":
        return None
    tower_name = parts[1]
    side = parts[2]
    if side not in {"Front", "Back"}:
        return None
    try:
        o_level = int(parts[3])
    except ValueError:
        return None
    return (tower_name, side, o_level)


def _normalize_mpk_target_keys(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in keys:
        parsed = parse_mpk_target_key(str(raw or ""))
        if parsed is None:
            continue
        key = f"mpk|{parsed[0]}|{parsed[1]}|{parsed[2]}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def get_mpk_locked_targets(db: Database) -> list[str]:
    raw = db.get_state(_MPK_LOCKED_TARGETS_KEY, "[]") or "[]"
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    normalized = _normalize_mpk_target_keys([str(x) for x in parsed])
    if normalized != parsed:
        db.set_state(_MPK_LOCKED_TARGETS_KEY, json.dumps(normalized, ensure_ascii=True))
    return normalized


def set_mpk_locked_targets(db: Database, keys: list[str]) -> list[str]:
    normalized = _normalize_mpk_target_keys(keys)
    db.set_state(_MPK_LOCKED_TARGETS_KEY, json.dumps(normalized, ensure_ascii=True))
    return normalized


def toggle_mpk_locked_target(
    db: Database, target_key: str, *, locked: bool | None = None
) -> list[str]:
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return get_mpk_locked_targets(db)
    norm_key = f"mpk|{parsed[0]}|{parsed[1]}|{parsed[2]}"
    current = get_mpk_locked_targets(db)
    as_set = set(current)
    if locked is None:
        if norm_key in as_set:
            as_set.remove(norm_key)
        else:
            as_set.add(norm_key)
    elif locked:
        as_set.add(norm_key)
    else:
        as_set.discard(norm_key)
    ordered = [k for k in current if k in as_set]
    for k in sorted(as_set):
        if k not in ordered:
            ordered.append(k)
    return set_mpk_locked_targets(db, ordered)


def format_mpk_target_label_from_key(target_key: str) -> str:
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return str(target_key or "")
    tower_name, side, o_level = parsed
    return f"{side} {tower_name} (O{o_level})"


def _mpk_target_stats(db: Database, parsed_key: tuple[str, str, int]) -> dict[str, Any]:
    tower_name, side, o_level = parsed_key
    row = db.query_one(
        """
        SELECT
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(attempt_source, 'practice') = 'mpk'
          AND COALESCE(tower_name, 'Unknown') = ?
          AND o_level = ?
          AND CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
              END = ?
        """,
        (tower_name, o_level, side),
    )
    attempts = _safe_int(row["attempts"]) if row is not None else 0
    successes = _safe_int(row["successes"]) if row is not None else 0
    return {
        "attempts": attempts,
        "successes": successes,
        "success_rate": round(_pct(successes, attempts), 2) if attempts > 0 else 0.0,
    }


def _mpk_selection_reason_label(reason: str) -> str:
    key = str(reason or "").strip().lower()
    if not key:
        return ""
    mapping = {
        "queued": "queued target",
        "unseen": "unseen target",
        "worst_success": "worst success rate",
        "mode": "mode strategy",
        "mode_fallback": "mode fallback",
        "weak_lock": "weak lock (streak)",
    }
    return mapping.get(key, "targeted")


def _max_finished_mpk_attempt_id(db: Database) -> int:
    row = db.query_one(
        """
        SELECT COALESCE(MAX(id), 0) AS max_id
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(attempt_source, 'practice') = 'mpk'
        """
    )
    if row is None:
        return 0
    return _safe_int(row["max_id"])


def _mpk_success_streak_for_target(db: Database, target_key: str, *, anchor_after_id: int = 0) -> int:
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return 0
    tower_name, side, o_level = parsed
    rows = db.query_all(
        """
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(attempt_source, 'practice') = 'mpk'
          AND id > ?
          AND COALESCE(tower_name, 'Unknown') = ?
          AND o_level = ?
          AND CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
              END = ?
        ORDER BY id DESC
        LIMIT 200
        """,
        (max(0, int(anchor_after_id)), tower_name, int(o_level), side),
    )
    streak = 0
    for row in rows:
        if str(row["status"]) == "success":
            streak += 1
        else:
            break
    return streak


def rotate_mpk_seed_for_target_key(
    db: Database, target_key: str, *, advance: bool
) -> dict[str, Any]:
    seed_map, map_levels, map_error = _load_mpk_seed_map()
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return {
            "selected_seed": None,
            "seed_changed": False,
            "seed_apply_error": "Invalid MPK target key.",
            "seed_pool_size": 0,
            "map_levels": map_levels,
            "map_error": map_error,
        }
    seed_pool = seed_map.get(parsed, [])
    if not seed_pool:
        return {
            "selected_seed": None,
            "seed_changed": False,
            "seed_apply_error": "No seeds available for selected MPK target.",
            "seed_pool_size": 0,
            "map_levels": map_levels,
            "map_error": map_error,
        }

    last_target_key = db.get_state("mpk.practice.target_key", "")
    last_seed_raw = db.get_state("mpk.practice.seed_value", "")
    try:
        cycle = int(db.get_state("mpk.practice.seed_cycle", "0") or "0")
    except ValueError:
        cycle = 0

    should_advance = (
        advance
        or target_key != last_target_key
        or not last_seed_raw
    )
    selected_seed: int
    seed_changed = False
    seed_apply_error: str | None = None
    if should_advance:
        selected_seed = int(seed_pool[cycle % len(seed_pool)])
        db.set_state("mpk.practice.seed_cycle", str(cycle + 1))
        db.set_state("mpk.practice.target_key", target_key)
        db.set_state("mpk.practice.seed_value", str(selected_seed))
        seed_changed = True
        atum_json_path = _resolve_runtime_atum_json_path(db)
        if atum_json_path is None:
            seed_apply_error = "No configured MPK instance path."
        else:
            seed_apply_error = _write_mpk_seed_to_atum_json_for_path(atum_json_path, selected_seed)
    else:
        try:
            selected_seed = int(last_seed_raw)
        except ValueError:
            selected_seed = int(seed_pool[0])
            db.set_state("mpk.practice.seed_value", str(selected_seed))

    return {
        "selected_seed": selected_seed,
        "seed_changed": seed_changed,
        "seed_apply_error": seed_apply_error,
        "seed_pool_size": len(seed_pool),
        "atum_json_path": str(_resolve_runtime_atum_json_path(db) or ""),
        "map_levels": map_levels,
        "map_error": map_error,
    }


def get_mpk_practice_candidates(
    db: Database,
    *,
    leniency_target: float | None = None,
) -> dict[str, Any]:
    if leniency_target is None:
        leniency_target = MPK_LENIENCY_TARGET_CTX.get()
    leniency_target = _normalize_leniency_target(leniency_target)
    seed_map, map_levels, map_error = _load_mpk_seed_map()
    leniency_lookup, leniency_error = _load_mpk_leniency_lookup()
    locked_targets = get_mpk_locked_targets(db)
    locked_target_set = set(locked_targets)
    if not seed_map:
        return {
            "seed_map": {},
            "map_levels": map_levels,
            "map_error": map_error,
            "leniency_error": leniency_error,
            "leniency_target": leniency_target,
            "candidates": [],
            "eligible_target_count": 0,
            "seed_target_count": 0,
            "locked_targets": locked_targets,
            "locked_filter_applied": False,
            "locked_matched_count": 0,
        }

    rows = db.query_all(
        """
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            o_level,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            MAX(id) AS last_attempt_id
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(attempt_source, 'practice') = 'mpk'
          AND o_level IS NOT NULL
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'
        GROUP BY COALESCE(tower_name, 'Unknown'), side, o_level
        """,
    )
    by_key: dict[tuple[str, str, int], dict[str, int]] = {}
    for row in rows:
        tower = str(row["tower_name"])
        side = str(row["side"])
        level = _safe_int(row["o_level"])
        if side not in {"Front", "Back"}:
            continue
        by_key[(tower, side, level)] = {
            "attempts": _safe_int(row["attempts"]),
            "successes": _safe_int(row["successes"]),
            "last_attempt_id": _safe_int(row["last_attempt_id"]),
        }

    ordered_keys = sorted(
        seed_map.keys(),
        key=lambda k: (
            _MPK_SIDE_ORDER.get(k[1], 99),
            _MPK_TOWER_ORDER.get(k[0], 999),
            k[2],
        ),
    )
    seed_target_count = len(ordered_keys)
    candidates: list[dict[str, Any]] = []
    for tower, side, level in ordered_keys:
        target_key = f"mpk|{tower}|{side}|{level}"
        is_locked_target = target_key in locked_target_set
        leniency_value = leniency_lookup.get((tower, side, level))
        if not is_locked_target and (leniency_value is None or leniency_value <= leniency_target):
            continue
        bucket = by_key.get((tower, side, level), {"attempts": 0, "successes": 0, "last_attempt_id": 0})
        attempts = int(bucket["attempts"])
        successes = int(bucket["successes"])
        last_attempt_id = int(bucket["last_attempt_id"])
        candidates.append(
            {
                "target_key": target_key,
                "tower_name": tower,
                "side": side,
                "o_level": level,
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2) if attempts > 0 else 0.0,
                "smoothed_success": round(((successes + 1.0) / (attempts + 2.0)) * 100.0, 2),
                "last_attempt_id": last_attempt_id,
                "leniency": round(float(leniency_value), 2) if leniency_value is not None else None,
                "is_locked": is_locked_target,
            }
        )
    locked_matched_count = sum(1 for c in candidates if bool(c.get("is_locked", False)))
    locked_filter_applied = False
    if locked_target_set and locked_matched_count > 0:
        candidates = [c for c in candidates if bool(c.get("is_locked", False))]
        locked_filter_applied = True
    return {
        "seed_map": seed_map,
        "map_levels": map_levels,
        "map_error": map_error,
        "leniency_error": leniency_error,
        "leniency_target": leniency_target,
        "candidates": candidates,
        "eligible_target_count": len(candidates),
        "seed_target_count": seed_target_count,
        "locked_targets": locked_targets,
        "locked_filter_applied": locked_filter_applied,
        "locked_matched_count": locked_matched_count,
    }


def _load_recent_mpk_targets(db: Database, *, max_items: int = 3) -> list[str]:
    raw = db.get_state(_MPK_RECENT_TARGETS_KEY, "[]") or "[]"
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    keys = [str(x) for x in parsed if str(x)]
    if max_items <= 0:
        return []
    return keys[-max_items:]


def _store_recent_mpk_targets(db: Database, keys: list[str], *, max_items: int = 3) -> None:
    trimmed = [str(x) for x in keys if str(x)]
    if max_items > 0:
        trimmed = trimmed[-max_items:]
    db.set_state(_MPK_RECENT_TARGETS_KEY, json.dumps(trimmed, ensure_ascii=True))


def _mpk_mode_schedule(coverage_percent: float) -> list[str]:
    if coverage_percent < 80.0:
        return ["fill"] * 5 + ["weak"] * 1 + ["maintain"] * 4
    if coverage_percent < 95.0:
        return ["fill"] * 3 + ["weak"] * 2 + ["maintain"] * 5
    return ["fill"] * 1 + ["weak"] * 4 + ["maintain"] * 5


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _MPK_SIDE_ORDER.get(str(candidate.get("side", "")), 99),
        _MPK_TOWER_ORDER.get(str(candidate.get("tower_name", "")), 999),
        int(candidate.get("o_level", 0)),
    )


def _pick_first_not_recent(bucket: list[dict[str, Any]], recent_keys: set[str]) -> dict[str, Any] | None:
    if not bucket:
        return None
    for row in bucket:
        if str(row.get("target_key", "")) not in recent_keys:
            return row
    return bucket[0]


def _pick_fill_candidate(
    bucket: list[dict[str, Any]],
    recent_keys: set[str],
    *,
    advance_mode: bool,
) -> dict[str, Any] | None:
    if not bucket:
        return None
    min_attempts = min(int(row.get("attempts", 0)) for row in bucket)
    pool = [row for row in bucket if int(row.get("attempts", 0)) == min_attempts]
    if not pool:
        return None
    non_recent = [row for row in pool if str(row.get("target_key", "")) not in recent_keys]
    candidate_pool = non_recent or pool
    if not advance_mode:
        # Keep preview deterministic when not advancing selection.
        return sorted(candidate_pool, key=_candidate_sort_key)[0]
    return random.choice(candidate_pool)


def _mode_for_candidate(candidate: dict[str, Any]) -> str:
    attempts = int(candidate.get("attempts", 0))
    if attempts < _MPK_MIN_SAMPLES_PER_TARGET:
        return "fill"
    success_rate = float(candidate.get("success_rate", 0.0))
    return "maintain" if success_rate >= 70.0 else "weak"


def _choose_mpk_target_with_modes(
    db: Database,
    candidates: list[dict[str, Any]],
    *,
    advance_mode: bool,
    prefer_queued: bool,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    by_key = {str(c["target_key"]): c for c in candidates}
    total_targets = len(candidates)
    qualified_targets = sum(
        1 for c in candidates if int(c.get("attempts", 0)) >= _MPK_MIN_SAMPLES_PER_TARGET
    )
    coverage_percent = round(_pct(qualified_targets, total_targets), 2)
    schedule = _mpk_mode_schedule(coverage_percent)
    try:
        cursor = int(db.get_state(_MPK_MODE_CURSOR_KEY, "0") or "0")
    except ValueError:
        cursor = 0
    requested_mode = schedule[cursor % len(schedule)] if schedule else "weak"
    queued_key = db.get_state("mpk.practice.target_key", "") or ""
    weak_lock_target_key = db.get_state(_MPK_WEAK_LOCK_TARGET_KEY, "") or ""
    try:
        weak_lock_anchor_attempt_id = int(
            db.get_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0") or "0"
        )
    except ValueError:
        weak_lock_anchor_attempt_id = 0
    if queued_key and queued_key not in by_key and advance_mode:
        db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, "")
        db.set_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0")
        weak_lock_target_key = ""
        weak_lock_anchor_attempt_id = 0
    if queued_key in by_key:
        queued_candidate = by_key[queued_key]
        queued_mode = _mode_for_candidate(queued_candidate)
        if queued_mode == "weak":
            if weak_lock_target_key == queued_key:
                anchor_after_id = weak_lock_anchor_attempt_id
            else:
                # In read-only/widget passes, missing lock state should not reset
                # streak visibility to 0; only advance pass anchors a new lock.
                anchor_after_id = _max_finished_mpk_attempt_id(db) if advance_mode else 0
                if advance_mode:
                    db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, queued_key)
                    db.set_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, str(anchor_after_id))
            weak_streak = _mpk_success_streak_for_target(
                db, queued_key, anchor_after_id=anchor_after_id
            )
            if weak_streak < _MPK_WEAK_MIN_STREAK_TO_SWAP:
                return {
                    "candidate": queued_candidate,
                    "selection_reason": "weak_lock",
                    "mode": "weak",
                    "requested_mode": requested_mode,
                    "coverage_percent": coverage_percent,
                    "qualified_targets": qualified_targets,
                    "total_targets": total_targets,
                    "min_samples_per_target": _MPK_MIN_SAMPLES_PER_TARGET,
                    "lock_applied": True,
                    "current_streak_on_recommended": weak_streak,
                    "min_streak_to_swap": _MPK_WEAK_MIN_STREAK_TO_SWAP,
                }
            if advance_mode:
                db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, "")
                db.set_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0")
        elif advance_mode and weak_lock_target_key == queued_key:
            db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, "")
            db.set_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0")
    if prefer_queued and queued_key in by_key:
        selected_mode = _mode_for_candidate(by_key[queued_key])
        return {
            "candidate": by_key[queued_key],
            "selection_reason": "queued",
            "mode": selected_mode,
            "requested_mode": requested_mode,
            "coverage_percent": coverage_percent,
            "qualified_targets": qualified_targets,
            "total_targets": total_targets,
            "min_samples_per_target": _MPK_MIN_SAMPLES_PER_TARGET,
            "lock_applied": False,
            "current_streak_on_recommended": 0,
            "min_streak_to_swap": _MPK_WEAK_MIN_STREAK_TO_SWAP,
        }
    mode = schedule[cursor % len(schedule)] if schedule else "weak"

    fill_bucket = sorted(
        (c for c in candidates if int(c.get("attempts", 0)) < _MPK_MIN_SAMPLES_PER_TARGET),
        key=lambda c: (int(c.get("attempts", 0)), *_candidate_sort_key(c)),
    )
    weak_bucket = sorted(
        (c for c in candidates if int(c.get("attempts", 0)) >= _MPK_MIN_SAMPLES_PER_TARGET),
        key=lambda c: (
            float(c.get("smoothed_success", 0.0)),
            -int(c.get("attempts", 0)),
            *_candidate_sort_key(c),
        ),
    )
    maintain_strong = [
        c
        for c in candidates
        if int(c.get("attempts", 0)) >= _MPK_MIN_SAMPLES_PER_TARGET
        and float(c.get("success_rate", 0.0)) >= 70.0
    ]
    maintain_bucket = sorted(
        (maintain_strong if maintain_strong else weak_bucket),
        key=lambda c: (
            int(c.get("last_attempt_id", 0)) if int(c.get("last_attempt_id", 0)) > 0 else 10**9,
            -float(c.get("success_rate", 0.0)),
            *_candidate_sort_key(c),
        ),
    )
    mode_to_bucket = {
        "fill": fill_bucket,
        "weak": weak_bucket,
        "maintain": maintain_bucket,
    }
    fallback_order = {
        "fill": ["fill", "weak", "maintain"],
        "weak": ["weak", "fill", "maintain"],
        "maintain": ["maintain", "weak", "fill"],
    }
    recent_keys = set(_load_recent_mpk_targets(db, max_items=3))
    selected: dict[str, Any] | None = None
    selected_mode = mode
    for candidate_mode in fallback_order.get(mode, ["weak", "fill", "maintain"]):
        bucket = mode_to_bucket.get(candidate_mode, [])
        if candidate_mode == "fill":
            selected = _pick_fill_candidate(bucket, recent_keys, advance_mode=advance_mode)
        else:
            selected = _pick_first_not_recent(bucket, recent_keys)
        if selected is not None:
            selected_mode = candidate_mode
            break
    if selected is None:
        return None

    if advance_mode:
        db.set_state(_MPK_MODE_CURSOR_KEY, str(cursor + 1))
        history = _load_recent_mpk_targets(db, max_items=3)
        history.append(str(selected["target_key"]))
        _store_recent_mpk_targets(db, history, max_items=3)
        db.set_state(_MPK_LAST_MODE_KEY, selected_mode)
        selected_key = str(selected["target_key"])
        # Weak lock is a weak-mode mechanic only. Do not (re)anchor lock state
        # from maintain/fill selections, even if candidate stats are weak.
        if selected_mode == "weak":
            if weak_lock_target_key != selected_key:
                db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, selected_key)
                db.set_state(
                    _MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY,
                    str(_max_finished_mpk_attempt_id(db)),
                )
        elif weak_lock_target_key:
            db.set_state(_MPK_WEAK_LOCK_TARGET_KEY, "")
            db.set_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0")

    reason = "mode"
    if selected_mode != mode:
        reason = "mode_fallback"

    return {
        "candidate": selected,
        "selection_reason": reason,
        "mode": selected_mode,
        "requested_mode": mode,
        "coverage_percent": coverage_percent,
        "qualified_targets": qualified_targets,
        "total_targets": total_targets,
        "min_samples_per_target": _MPK_MIN_SAMPLES_PER_TARGET,
        "lock_applied": False,
        "current_streak_on_recommended": 0,
        "min_streak_to_swap": _MPK_WEAK_MIN_STREAK_TO_SWAP,
    }


def select_next_mpk_target(
    db: Database,
    *,
    leniency_target: float | None = None,
) -> dict[str, Any]:
    prep = get_mpk_practice_candidates(db, leniency_target=leniency_target)
    candidates = prep.get("candidates", [])
    pick = _choose_mpk_target_with_modes(
        db,
        candidates,
        advance_mode=True,
        prefer_queued=False,
    )
    return {
        "prep": prep,
        "pick": pick,
    }


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _scope_where(
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
    include_where: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if zero_type is not None:
        clauses.append("COALESCE(zero_type, 'Unknown') = ?")
        params.append(zero_type)
    if tower_name is not None:
        clauses.append("COALESCE(tower_name, 'Unknown') = ?")
        params.append(tower_name)
    if front_back is not None:
        if front_back == "Front":
            clauses.append("COALESCE(zero_type, '') LIKE 'Front %'")
        elif front_back == "Back":
            clauses.append("COALESCE(zero_type, '') LIKE 'Back %'")
        else:
            clauses.append(
                "COALESCE(zero_type, '') NOT LIKE 'Front %' AND COALESCE(zero_type, '') NOT LIKE 'Back %'"
            )
    if not INCLUDE_STRAIGHT_CTX.get():
        clauses.append("COALESCE(zero_type, '') NOT LIKE '%Straight%'")
    rotation_filter = ROTATION_FILTER_CTX.get()
    if rotation_filter == "cw":
        clauses.append(
            "UPPER(COALESCE(zero_type, '')) LIKE '%CW' AND UPPER(COALESCE(zero_type, '')) NOT LIKE '%CCW'"
        )
    elif rotation_filter == "ccw":
        clauses.append("UPPER(COALESCE(zero_type, '')) LIKE '%CCW'")
    attempt_source = ATTEMPT_SOURCE_CTX.get()
    if attempt_source in {"practice", "mpk"}:
        clauses.append("COALESCE(attempt_source, 'practice') = ?")
        params.append(attempt_source)

    window_min_id = WINDOW_MIN_ID_CTX.get()
    if window_min_id is not None:
        clauses.append("id >= ?")
        params.append(window_min_id)
    window_start_utc = WINDOW_START_UTC_CTX.get()
    if window_start_utc is not None:
        clauses.append("started_at_utc >= ?")
        params.append(window_start_utc)

    if not clauses:
        return ("", [])
    prefix = "WHERE" if include_where else "AND"
    return (f" {prefix} " + " AND ".join(clauses), params)


def _type_where(zero_type: str | None, include_where: bool = True) -> tuple[str, list[Any]]:
    return _scope_where(zero_type=zero_type, include_where=include_where)


def _compute_current_session_start_utc(db: Database) -> str | None:
    where, params = _scope_where(include_where=False)
    row = db.query_one(
        f"""
        WITH finished AS (
            SELECT id, started_at_utc
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND started_at_utc IS NOT NULL{where}
            ORDER BY started_at_utc ASC, id ASC
        ),
        marks AS (
            SELECT
                id,
                started_at_utc,
                CASE
                    WHEN LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) IS NULL THEN 1
                    WHEN (
                        julianday(started_at_utc)
                        - julianday(LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC))
                    ) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM finished
        ),
        numbered AS (
            SELECT
                started_at_utc,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM marks
        )
        SELECT MIN(started_at_utc) AS session_start_utc
        FROM numbered
        WHERE session_index = (SELECT MAX(session_index) FROM numbered)
        """,
        params,
    )
    if row is None:
        return None
    value = row["session_start_utc"]
    return str(value) if value is not None else None


def _compute_current_session_start_utc_unscoped(db: Database) -> str | None:
    row = db.query_one(
        """
        WITH finished AS (
            SELECT id, started_at_utc
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND started_at_utc IS NOT NULL
            ORDER BY started_at_utc ASC, id ASC
        ),
        marks AS (
            SELECT
                id,
                started_at_utc,
                CASE
                    WHEN LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) IS NULL THEN 1
                    WHEN (
                        julianday(started_at_utc)
                        - julianday(LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC))
                    ) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM finished
        ),
        numbered AS (
            SELECT
                started_at_utc,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM marks
        )
        SELECT MIN(started_at_utc) AS session_start_utc
        FROM numbered
        WHERE session_index = (SELECT MAX(session_index) FROM numbered)
        """
    )
    if row is None:
        return None
    value = row["session_start_utc"]
    return str(value) if value is not None else None


def _compute_window_bounds(db: Database) -> dict[str, dict[str, Any]]:
    bounds: dict[str, dict[str, Any]] = {
        "all": {"min_id": None, "start_utc": None},
        "current_session": {"min_id": None, "start_utc": _compute_current_session_start_utc(db)},
    }
    for n in (10, 25, 50, 100):
        where, params = _scope_where(include_where=False)
        row = db.query_one(
            f"""
            SELECT MIN(id) AS min_id
            FROM (
                SELECT id
                FROM attempts
                WHERE status IN ('success', 'fail'){where}
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (*params, n),
        )
        min_id = int(row["min_id"]) if row is not None and row["min_id"] is not None else None
        bounds[f"last_{n}"] = {"min_id": min_id, "start_utc": None}
    return bounds


def compute_mpk_practice_next_widget(db: Database) -> dict[str, Any]:
    leniency_target = _normalize_leniency_target(MPK_LENIENCY_TARGET_CTX.get())
    prep = get_mpk_practice_candidates(db, leniency_target=leniency_target)
    seed_map = prep["seed_map"]
    map_levels = prep["map_levels"]
    map_error = prep["map_error"]
    leniency_error = prep.get("leniency_error")
    candidates = prep["candidates"]
    leniency_lookup, _ = _load_mpk_leniency_lookup()
    eligible_target_count = int(prep.get("eligible_target_count", len(candidates)))
    seed_target_count = int(prep.get("seed_target_count", len(seed_map)))
    max_leniency = max(
        (float(c.get("leniency")) for c in candidates if c.get("leniency") is not None),
        default=0.0,
    )
    locked_targets = [str(x) for x in prep.get("locked_targets", [])]
    locked_target_labels = [format_mpk_target_label_from_key(k) for k in locked_targets]
    locked_filter_applied = bool(prep.get("locked_filter_applied", False))
    locked_matched_count = int(prep.get("locked_matched_count", 0))
    if not seed_map:
        return {
            "disabled": True,
            "disabled_reason": map_error or "No MPK seed map entries available.",
            "recommended": None,
            "window_size": 0,
        }

    if not candidates:
        reasons: list[str] = []
        if leniency_error:
            reasons.append(str(leniency_error))
        reasons.append(
            f"No MPK targets pass leniency filter > {leniency_target:.2f} at Y94/Y95."
        )
        return {
            "disabled": True,
            "disabled_reason": " ".join(reasons),
            "recommended": None,
            "window_size": 0,
        }

    pick = _choose_mpk_target_with_modes(
        db,
        candidates,
        advance_mode=False,
        prefer_queued=True,
    )
    if pick is None:
        return {
            "disabled": True,
            "disabled_reason": "No MPK target could be selected from candidate pool.",
            "recommended": None,
            "window_size": 0,
        }
    recommended_raw = pick["candidate"]
    selection_reason = str(pick.get("selection_reason", "mode"))
    selected_mode = str(pick.get("mode", db.get_state(_MPK_LAST_MODE_KEY, "") or "weak"))
    requested_mode = str(pick.get("requested_mode", selected_mode))
    qualified_targets = int(pick.get("qualified_targets", 0))
    total_targets = int(pick.get("total_targets", len(candidates)))
    mode_coverage_pct = float(pick.get("coverage_percent", 0.0))
    min_samples_per_target = int(pick.get("min_samples_per_target", _MPK_MIN_SAMPLES_PER_TARGET))
    lock_applied = bool(pick.get("lock_applied", False))
    current_streak_on_recommended = int(pick.get("current_streak_on_recommended", 0))
    min_streak_to_swap = int(pick.get("min_streak_to_swap", _MPK_WEAK_MIN_STREAK_TO_SWAP))
    weak_lock_target_key = db.get_state(_MPK_WEAK_LOCK_TARGET_KEY, "") or ""
    try:
        weak_lock_anchor_attempt_id = int(
            db.get_state(_MPK_WEAK_LOCK_ANCHOR_ATTEMPT_ID_KEY, "0") or "0"
        )
    except ValueError:
        weak_lock_anchor_attempt_id = 0

    def _weak_status_for_target(target_key: str) -> dict[str, Any] | None:
        if not target_key:
            return None
        anchor_after_id = weak_lock_anchor_attempt_id if weak_lock_target_key == target_key else 0
        streak = _mpk_success_streak_for_target(
            db,
            target_key,
            anchor_after_id=anchor_after_id,
        )
        return {
            "streak": int(streak),
            "min_streak": int(min_streak_to_swap),
            "remaining": max(0, int(min_streak_to_swap) - int(streak)),
            "lock_target": bool(weak_lock_target_key == target_key),
            "anchor_after_id": int(anchor_after_id),
        }

    unseen = [c for c in candidates if int(c.get("attempts", 0)) == 0]

    rec_key = str(recommended_raw["target_key"])
    parsed_key = parse_mpk_target_key(rec_key)
    if parsed_key is None:
        return {
            "disabled": True,
            "disabled_reason": "Invalid MPK target key while selecting recommendation.",
            "recommended": None,
            "window_size": 0,
        }
    rec_key_tuple = parsed_key
    weak_status = _weak_status_for_target(rec_key) if selected_mode == "weak" else None
    if weak_status is not None and lock_applied:
        weak_status["streak"] = int(current_streak_on_recommended)
        weak_status["remaining"] = max(
            0, int(min_streak_to_swap) - int(current_streak_on_recommended)
        )
    seed_state = rotate_mpk_seed_for_target_key(db, rec_key, advance=False)
    selected_seed_raw = seed_state.get("selected_seed")
    selected_seed = int(selected_seed_raw) if selected_seed_raw is not None else None
    seed_changed = bool(seed_state.get("seed_changed", False))
    seed_apply_error = (
        str(seed_state.get("seed_apply_error"))
        if seed_state.get("seed_apply_error") is not None
        else None
    )
    seed_pool_size = int(seed_state.get("seed_pool_size", 0))

    attempted_targets = sum(1 for c in candidates if c["attempts"] > 0)
    coverage_pct = round(_pct(attempted_targets, total_targets), 2)
    missing_labels = [
        f"{c['tower_name']} {c['side']} O{c['o_level']}"
        for c in unseen[:30]
    ]
    if len(unseen) > 30:
        missing_labels.append(f"... +{len(unseen) - 30} more")
    missing_groups: list[str] = []
    grouped_levels: dict[tuple[str, str], list[int]] = {}
    for c in unseen:
        key = (str(c["tower_name"]), str(c["side"]))
        grouped_levels.setdefault(key, []).append(int(c["o_level"]))
    for (tower_name, side), levels in grouped_levels.items():
        unique_levels = sorted(set(levels))
        level_text = ", ".join(f"O{lvl}" for lvl in unique_levels)
        missing_groups.append(f"{side} {tower_name}: {level_text}")
    max_groups = 14
    if len(missing_groups) > max_groups:
        hidden = len(missing_groups) - max_groups
        missing_groups = [*missing_groups[:max_groups], f"... +{hidden} more groups"]

    recommended = {
        "target_kind": "mpk_zero",
        "label": f"{rec_key_tuple[1]} {rec_key_tuple[0]} O{rec_key_tuple[2]}",
        "tower_name": rec_key_tuple[0],
        "side": rec_key_tuple[1],
        "rotation": f"O{rec_key_tuple[2]}",
        "o_level": rec_key_tuple[2],
        "attempts": int(recommended_raw["attempts"]),
        "successes": int(recommended_raw["successes"]),
        "success_rate": float(recommended_raw["success_rate"]),
        "leniency": float(recommended_raw.get("leniency", 0.0)),
        "selection_reason": selection_reason,
        "selection_reason_label": _mpk_selection_reason_label(selection_reason),
        "selection_mode": selected_mode,
        "requested_mode": requested_mode,
        "selected_seed": str(selected_seed) if selected_seed is not None else "",
        "chat_command": str(selected_seed) if selected_seed is not None else "",
        "weak_streak": int(weak_status["streak"]) if weak_status is not None else None,
        "weak_min_streak": int(weak_status["min_streak"]) if weak_status is not None else None,
        "weak_remaining": int(weak_status["remaining"]) if weak_status is not None else None,
        "weak_lock_active": bool(weak_status["lock_target"]) if weak_status is not None else False,
    }
    current_target_key = db.get_state("mpk.practice.current_target_key", "") or ""
    current_seed_value = db.get_state("mpk.practice.current_seed_value", "") or ""
    current_world_name = db.get_state("mpk.practice.current_world_name", "") or ""
    current_reason = db.get_state("mpk.practice.current_selection_reason", "") or ""
    current_mode = db.get_state("mpk.practice.current_selection_mode", "") or ""
    current_requested_mode = db.get_state("mpk.practice.current_requested_mode", "") or ""
    parsed_current_key = parse_mpk_target_key(current_target_key)
    current_practice: dict[str, Any] | None = None
    if parsed_current_key is not None:
        current_stats = _mpk_target_stats(db, parsed_current_key)
        current_leniency = leniency_lookup.get(parsed_current_key)
        current_weak_status = (
            _weak_status_for_target(current_target_key)
            if current_mode == "weak"
            else None
        )
        current_practice = {
            "target_key": current_target_key,
            "tower_name": parsed_current_key[0],
            "side": parsed_current_key[1],
            "o_level": parsed_current_key[2],
            "label": f"{parsed_current_key[0]} {parsed_current_key[1]} O{parsed_current_key[2]}",
            "seed_value": current_seed_value,
            "world_name": current_world_name,
            "attempts": int(current_stats["attempts"]),
            "successes": int(current_stats["successes"]),
            "success_rate": float(current_stats["success_rate"]),
            "leniency": round(float(current_leniency), 2) if current_leniency is not None else None,
            "selection_reason": current_reason,
            "selection_reason_label": _mpk_selection_reason_label(current_reason),
            "selection_mode": current_mode,
            "requested_mode": current_requested_mode,
            "weak_streak": int(current_weak_status["streak"])
            if current_weak_status is not None
            else None,
            "weak_min_streak": int(current_weak_status["min_streak"])
            if current_weak_status is not None
            else None,
            "weak_remaining": int(current_weak_status["remaining"])
            if current_weak_status is not None
            else None,
            "weak_lock_active": bool(current_weak_status["lock_target"])
            if current_weak_status is not None
            else False,
        }
    elif current_seed_value:
        current_practice = {
            "target_key": "",
            "tower_name": "Unknown",
            "side": "Unknown",
            "o_level": None,
            "label": "Unknown target",
            "seed_value": current_seed_value,
            "world_name": current_world_name,
            "attempts": 0,
            "successes": 0,
            "success_rate": 0.0,
            "leniency": None,
            "selection_reason": current_reason,
            "selection_reason_label": _mpk_selection_reason_label(current_reason),
            "selection_mode": current_mode,
            "requested_mode": current_requested_mode,
        }

    return {
        "min_id": WINDOW_MIN_ID_CTX.get(),
        "window_size": 0,
        "recommended": recommended,
        "next_practice": recommended,
        "current_practice": current_practice,
        "lock_applied": lock_applied,
        "current_streak_on_recommended": current_streak_on_recommended,
        "min_streak_to_swap": min_streak_to_swap,
        "distribution": {
            "coverage_percent": coverage_pct,
            "threshold_percent": 100.0,
            "min_points_per_target": min_samples_per_target,
            "qualified_targets": attempted_targets,
            "total_targets": total_targets,
            "is_sufficient": attempted_targets >= total_targets,
            "eligible_targets": eligible_target_count,
            "seed_targets": seed_target_count,
            "mode_coverage_percent": round(mode_coverage_pct, 2),
            "mode_qualified_targets": qualified_targets,
            "mode_total_targets": total_targets,
            "max_leniency": round(max_leniency, 2),
            "locked_filter_applied": locked_filter_applied,
            "locked_matched_count": locked_matched_count,
        },
        "missing_towers": missing_labels,
        "missing_target_groups": missing_groups,
        "missing_1_8_groups": [],
        "source": "mpk",
        "selection_reason": selection_reason,
        "selection_mode": selected_mode,
        "requested_mode": requested_mode,
        "weak_streak_status": weak_status,
        "leniency_target": leniency_target,
        "locked_target_keys": locked_targets,
        "locked_target_labels": locked_target_labels,
        "lock_list_active": len(locked_targets) > 0,
        "seed_status": {
            "seed_changed": seed_changed,
            "seed_apply_error": seed_apply_error,
            "seed_pool_size": seed_pool_size,
            "atum_json_path": str(seed_state.get("atum_json_path", "")),
            "seeds_map_path": str(MPK_SEEDS_MAP_PATH),
            "o_levels_in_map": seed_state.get("map_levels", map_levels),
            "map_error": seed_state.get("map_error", map_error),
            "leniency_error": leniency_error,
        },
    }


def compute_practice_next_widget(db: Database) -> dict[str, Any]:
    return compute_mpk_practice_next_widget(db)


def compute_type_overview(db: Database) -> list[dict[str, Any]]:
    rows = db.query_all(
        """
        SELECT
            COALESCE(zero_type, 'Unknown') AS zero_type,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN status IN ('success','fail') AND bed_count > 0 THEN 1 ELSE 0 END) AS setup_reached,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        GROUP BY COALESCE(zero_type, 'Unknown')
        ORDER BY attempts DESC, zero_type ASC
        """
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        setup_reached = _safe_int(row["setup_reached"])
        result.append(
            {
                "zero_type": row["zero_type"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "in_progress": _safe_int(row["in_progress"]),
                "setup_reached": setup_reached,
                "success_rate": round(_pct(successes, finished), 2),
                "setup_reach_rate": round(_pct(setup_reached, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_streaks(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where.replace(' WHERE', ' AND')}
        ORDER BY id ASC
        """,
        params,
    )
    current_success_streak = 0
    best_success_streak = 0
    running = 0
    for row in rows:
        if row["status"] == "success":
            running += 1
            best_success_streak = max(best_success_streak, running)
        else:
            running = 0

    for row in reversed(rows):
        if row["status"] == "success":
            current_success_streak += 1
        else:
            break

    return {
        "current_success_streak": current_success_streak,
        "best_success_streak": best_success_streak,
    }


def compute_window_consistency(
    db: Database,
    window_size: int,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float | int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where.replace(' WHERE', ' AND')}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, window_size),
    )
    total = len(rows)
    successes = sum(1 for row in rows if row["status"] == "success")
    return {
        "window": window_size,
        "attempts": total,
        "successes": successes,
        "success_rate": round(_pct(successes, total), 2),
    }


def compute_summary(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    totals = db.query_one(
        f"""
        SELECT
            COUNT(*) AS total_attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(first_bed_seconds) AS avg_first_bed_seconds,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data,
            MIN(CASE WHEN status = 'success' THEN explosives_used END) AS best_rotations_success,
            MIN(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN explosives_used + COALESCE(explosives_left, 0)
                END
            ) AS best_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used = 2 AND explosives_left = 2
                    THEN 1
                    ELSE 0
                END
            ) AS perfect_2_2_count
        FROM attempts
        {where}
        """,
        params,
    )
    totals = totals or {}
    successes = _safe_int(totals["successes"])
    failures = _safe_int(totals["failures"])
    finished = successes + failures
    medians = compute_medians(
        db, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )
    recent = compute_recent_window(
        db, 20, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )

    return {
        "total_attempts": _safe_int(totals["total_attempts"]),
        "successes": successes,
        "failures": failures,
        "finished_attempts": finished,
        "success_rate": round(_pct(successes, finished), 2),
        "avg_first_bed_seconds": round(_safe_float(totals["avg_first_bed_seconds"]), 2),
        "avg_success_time_seconds": round(_safe_float(totals["avg_success_time"]), 2),
        "avg_damage_per_bed": round(_safe_float(totals["avg_damage_per_bed"]), 2),
        "avg_rotations_success": _round_or_none(totals["avg_rotations_success"]),
        "avg_total_explosives_success": _round_or_none(totals["avg_total_explosives_success"]),
        "successes_with_rotation_data": _safe_int(totals["successes_with_rotation_data"]),
        "successes_with_explosives_data": _safe_int(totals["successes_with_explosives_data"]),
        "best_rotations_success": _safe_int(totals["best_rotations_success"]),
        "best_total_explosives_success": _safe_int(totals["best_total_explosives_success"]),
        "perfect_2_2_count": _safe_int(totals["perfect_2_2_count"]),
        "perfect_2_2_rate_among_successes": round(
            _pct(_safe_int(totals["perfect_2_2_count"]), successes), 2
        ),
        "median_success_time_seconds": medians["median_success_time_seconds"],
        "median_damage_per_bed": medians["median_damage_per_bed"],
        "recent_success_rate": recent["success_rate"],
        "recent_avg_success_time_seconds": recent["avg_success_time_seconds"],
        "recent_avg_damage_per_bed": recent["avg_damage_per_bed"],
    }


def compute_damage_per_bed(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        WITH filtered_attempts AS (
            SELECT id
            FROM attempts
            {where}
        ),
        major_hits AS (
            SELECT
                b.attempt_id,
                b.damage,
                ROW_NUMBER() OVER (PARTITION BY b.attempt_id ORDER BY b.id ASC) AS major_hit_index
            FROM attempt_beds b
            JOIN filtered_attempts f ON f.id = b.attempt_id
            WHERE b.is_major = 1
        )
        SELECT
            major_hit_index AS bed_number,
            COUNT(*) AS hits,
            AVG(damage) AS avg_damage,
            MAX(damage) AS max_damage,
            MIN(damage) AS min_damage
        FROM major_hits
        GROUP BY major_hit_index
        ORDER BY major_hit_index ASC
        """,
        params,
    )
    return [
        {
            "bed_number": _safe_int(row["bed_number"]),
            "hits": _safe_int(row["hits"]),
            "avg_damage": round(_safe_float(row["avg_damage"]), 2),
            "max_damage": _safe_int(row["max_damage"]),
            "min_damage": _safe_int(row["min_damage"]),
        }
        for row in rows
    ]


def build_dashboard_payload_selected(
    db: Database,
    *,
    include_1_8: bool = False,
    rotation: str = "both",
    window: str = "all",
    tower_name: str | None = None,
    front_back: str | None = None,
    attempt_source: str = "mpk",
    leniency_target: float = 0.0,
    detail: str = "full",
) -> dict[str, Any]:
    detail = detail.lower().strip()
    if detail not in {"light", "full"}:
        detail = "full"
    rotation = rotation.lower().strip()
    if rotation not in {"both", "cw", "ccw"}:
        rotation = "both"
    window = window.lower().strip()
    if window not in {"all", "current_session", "last_10", "last_25", "last_50", "last_100"}:
        window = "all"
    attempt_source = "mpk"
    leniency_target = _normalize_leniency_target(leniency_target)

    tok_straight = INCLUDE_STRAIGHT_CTX.set(include_1_8)
    tok_rotation = ROTATION_FILTER_CTX.set(rotation)
    tok_source = ATTEMPT_SOURCE_CTX.set(attempt_source)
    tok_leniency = MPK_LENIENCY_TARGET_CTX.set(leniency_target)
    try:
        db.set_state("mpk.practice.leniency_target", str(leniency_target))
        bounds_by_window = _compute_window_bounds(db)
        bounds = bounds_by_window.get(window, {"min_id": None, "start_utc": None})
        tok_id = WINDOW_MIN_ID_CTX.set(bounds.get("min_id"))
        tok_start = WINDOW_START_UTC_CTX.set(bounds.get("start_utc"))
        try:
            tower_front_back_overview = compute_tower_front_back_overview(db)
            available_towers = sorted(
                {
                    row["tower_name"]
                    for row in tower_front_back_overview
                    if row["tower_name"] not in {"", "Unknown"}
                }
            )
            available_front_backs = sorted(
                {
                    row["front_back"]
                    for row in tower_front_back_overview
                    if row["front_back"] in {"Front", "Back"}
                }
            )

            window_options = [
                {"key": "all", "label": "All"},
                {"key": "current_session", "label": "Current Session"},
                {"key": "last_10", "label": "Last 10"},
                {"key": "last_25", "label": "Last 25"},
                {"key": "last_50", "label": "Last 50"},
                {"key": "last_100", "label": "Last 100"},
            ]

            scope: dict[str, Any] = {
                "summary": compute_summary(db, tower_name=tower_name, front_back=front_back),
                "streaks": compute_streaks(db, tower_name=tower_name, front_back=front_back),
                "bests": compute_best_and_recent(db, tower_name=tower_name, front_back=front_back),
                "consistency_windows": [
                    compute_window_consistency(db, 10, tower_name=tower_name, front_back=front_back),
                    compute_window_consistency(db, 25, tower_name=tower_name, front_back=front_back),
                    compute_window_consistency(db, 50, tower_name=tower_name, front_back=front_back),
                ],
            }

            if detail == "full":
                scope.update(
                    {
                        "damage_per_bed": compute_damage_per_bed(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "tower_performance": compute_tower_performance(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "tower_type_breakdown": compute_tower_type_breakdown(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "session_progression": compute_session_progression(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "time_series": compute_time_series(
                            db, limit=200, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_10": compute_rolling_consistency(
                            db, window_size=10, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_25": compute_rolling_consistency(
                            db, window_size=25, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_50": compute_rolling_consistency(
                            db, window_size=50, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "outcome_runs": compute_outcome_runs(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "speed_bins": compute_speed_bins(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "attempts_by_session": compute_attempts_by_session(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "o_level_consistency": compute_o_level_consistency(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "o_level_heatmap": compute_o_level_heatmap(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "standing_height_consistency": compute_standing_height_consistency(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "recent_attempts": compute_recent_attempts(
                            db, limit=60, tower_name=tower_name, front_back=front_back
                        ),
                    }
                )
            else:
                scope.update(
                    {
                        "damage_per_bed": [],
                        "tower_performance": [],
                        "tower_type_breakdown": [],
                        "session_progression": [],
                        "time_series": [],
                        "rolling_consistency_10": [],
                        "rolling_consistency_25": [],
                        "rolling_consistency_50": [],
                        "outcome_runs": {"runs": [], "best_success_run": 0, "best_fail_run": 0},
                        "speed_bins": [],
                        "attempts_by_session": [],
                        "o_level_consistency": [],
                        "o_level_heatmap": [],
                        "standing_height_consistency": [],
                        "recent_attempts": [],
                    }
                )

            payload = {
                "scope": scope,
                "tower_front_back_overview": tower_front_back_overview,
                "tower_radar": compute_tower_radar(db) if detail == "full" else {"front": [], "back": []},
                "available_towers": available_towers,
                "available_front_backs": available_front_backs,
                "window_options": window_options,
                "selected_window": window,
                "selected_rotation": rotation,
                "selected_include_1_8": include_1_8,
                "selected_attempt_source": attempt_source,
                "selected_leniency_target": leniency_target,
                "attempt_source_options": [{"key": "mpk", "label": "MPK Seeds"}],
                "detail": detail,
                "practice_next": compute_practice_next_widget(db),
            }
            return payload
        finally:
            WINDOW_MIN_ID_CTX.reset(tok_id)
            WINDOW_START_UTC_CTX.reset(tok_start)
    finally:
        ROTATION_FILTER_CTX.reset(tok_rotation)
        INCLUDE_STRAIGHT_CTX.reset(tok_straight)
        ATTEMPT_SOURCE_CTX.reset(tok_source)
        MPK_LENIENCY_TARGET_CTX.reset(tok_leniency)


def compute_tower_performance(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used = 2 AND explosives_left = 2
                    THEN 1
                    ELSE 0
                END
            ) AS perfect_2_2_count
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back
        ORDER BY attempts DESC, tower_name ASC, front_back ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
                "avg_damage_per_bed": round(_safe_float(row["avg_damage_per_bed"]), 2),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
                "successes_with_rotation_data": _safe_int(row["successes_with_rotation_data"]),
                "successes_with_explosives_data": _safe_int(row["successes_with_explosives_data"]),
                "perfect_2_2_count": _safe_int(row["perfect_2_2_count"]),
            }
        )
    return result


def compute_tower_type_breakdown(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COALESCE(zero_type, 'Unknown') AS zero_type,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back, COALESCE(zero_type, 'Unknown')
        ORDER BY tower_name ASC, front_back ASC, attempts DESC, zero_type ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "zero_type": row["zero_type"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
                "avg_damage_per_bed": round(_safe_float(row["avg_damage_per_bed"]), 2),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
                "successes_with_rotation_data": _safe_int(row["successes_with_rotation_data"]),
                "successes_with_explosives_data": _safe_int(row["successes_with_explosives_data"]),
            }
        )
    return result


def compute_recent_attempts(
    db: Database,
    limit: int = 40,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            id,
            COALESCE(attempt_source, 'practice') AS attempt_source,
            status,
            fail_reason,
            started_at_utc,
            started_clock,
            ended_at_utc,
            ended_clock,
            first_bed_seconds,
            success_time_seconds,
            tower_name,
            tower_code,
            zero_type,
            standing_height,
            explosives_used,
            explosives_left,
            bed_count,
            beds_exploded,
            anchors_exploded,
            bow_shots,
            crossbow_shots,
            total_damage,
            major_damage_total,
            major_hit_count,
            o_level,
            flyaway_detected,
            flyaway_gt,
            flyaway_dragon_y,
            flyaway_node,
            flyaway_crystals_alive
        FROM attempts
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        bed_count = _safe_int(row["bed_count"])
        total_damage = _safe_int(row["total_damage"])
        major_hit_count = _safe_int(row["major_hit_count"])
        major_damage_per_hit = (
            _safe_float(row["major_damage_total"]) / major_hit_count if major_hit_count > 0 else 0.0
        )
        rotations = row["explosives_used"]
        explosives_left = row["explosives_left"]
        total_explosives = (
            (int(rotations) + (int(explosives_left) if explosives_left is not None else 0))
            if rotations is not None
            else None
        )
        zero_type_text = str(row["zero_type"] or "")
        is_1_8 = "Straight" in zero_type_text
        result.append(
            {
                "id": _safe_int(row["id"]),
                "attempt_source": str(row["attempt_source"] or "practice"),
                "status": row["status"],
                "fail_reason": row["fail_reason"],
                "started_at_utc": row["started_at_utc"],
                "started_clock": row["started_clock"],
                "ended_at_utc": row["ended_at_utc"],
                "ended_clock": row["ended_clock"],
                "first_bed_seconds": round(_safe_float(row["first_bed_seconds"]), 2),
                "success_time_seconds": round(_safe_float(row["success_time_seconds"]), 2),
                "tower_name": row["tower_name"] or "Unknown",
                "tower_code": row["tower_code"],
                "zero_type": zero_type_text or "Unknown",
                "is_1_8": is_1_8,
                "standing_height": _safe_int(row["standing_height"])
                if row["standing_height"] is not None
                else None,
                "rotations": _safe_int(rotations) if rotations is not None else None,
                "explosives_left": _safe_int(explosives_left) if explosives_left is not None else None,
                "total_explosives": total_explosives,
                "is_perfect_2_2": rotations == 2 and explosives_left == 2,
                "bed_count": bed_count,
                "beds_exploded": _safe_int(row["beds_exploded"]),
                "anchors_exploded": _safe_int(row["anchors_exploded"]),
                "bow_shots": _safe_int(row["bow_shots"]),
                "crossbow_shots": _safe_int(row["crossbow_shots"]),
                "bow_shots_total": _safe_int(row["bow_shots"]) + _safe_int(row["crossbow_shots"]),
                "total_damage": total_damage,
                "major_hit_count": major_hit_count,
                "major_damage_per_hit": round(major_damage_per_hit, 2),
                "o_level": _safe_int(row["o_level"]) if row["o_level"] is not None else None,
                "flyaway_detected": bool(_safe_int(row["flyaway_detected"])),
                "flyaway_gt": _safe_int(row["flyaway_gt"]),
                "flyaway_dragon_y": _safe_int(row["flyaway_dragon_y"])
                if row["flyaway_dragon_y"] is not None
                else None,
                "flyaway_node": str(row["flyaway_node"] or ""),
                "flyaway_crystals_alive": _safe_int(row["flyaway_crystals_alive"])
                if row["flyaway_crystals_alive"] is not None
                else None,
            }
        )
    return result


def compute_session_progression(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        WITH scoped AS (
            SELECT
                id,
                started_at_utc,
                status,
                success_time_seconds,
                explosives_used,
                explosives_left
            FROM attempts
            WHERE status IN ('success', 'fail'){where}
            ORDER BY started_at_utc ASC, id ASC
        ),
        with_prev AS (
            SELECT
                *,
                LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) AS prev_started_at_utc
            FROM scoped
        ),
        with_sessions AS (
            SELECT
                *,
                CASE
                    WHEN prev_started_at_utc IS NULL THEN 1
                    WHEN (julianday(started_at_utc) - julianday(prev_started_at_utc)) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM with_prev
        ),
        numbered AS (
            SELECT
                *,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM with_sessions
        )
        SELECT
            session_index,
            MIN(started_at_utc) AS session_start_utc,
            MAX(started_at_utc) AS session_last_attempt_utc,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time_seconds,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success
        FROM numbered
        GROUP BY session_index
        ORDER BY session_index ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "session_index": _safe_int(row["session_index"]),
                "session_label": f"S{_safe_int(row['session_index'])}",
                "session_start_utc": row["session_start_utc"],
                "session_last_attempt_utc": row["session_last_attempt_utc"],
                "attempts": attempts,
                "successes": successes,
                "failures": _safe_int(row["failures"]),
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": _round_or_none(row["avg_success_time_seconds"]),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
            }
        )
    return result


def compute_medians(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    success_times = [
        _safe_float(row["success_time_seconds"])
        for row in db.query_all(
            f"""
            SELECT success_time_seconds
            FROM attempts
            WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
            ORDER BY success_time_seconds ASC
            """,
            params,
        )
    ]
    first_beds = [
        _safe_float(row["first_bed_seconds"])
        for row in db.query_all(
            f"""
            SELECT first_bed_seconds
            FROM attempts
            WHERE first_bed_seconds IS NOT NULL{where_success}
            ORDER BY first_bed_seconds ASC
            """,
            params,
        )
    ]
    damage_per_bed = [
        _safe_float(row["damage_per_bed"])
        for row in db.query_all(
            f"""
            SELECT CAST(major_damage_total AS REAL) / major_hit_count AS damage_per_bed
            FROM attempts
            WHERE major_hit_count > 0{where_success}
            ORDER BY damage_per_bed ASC
            """,
            params,
        )
    ]

    def median(values: list[float]) -> float:
        if not values:
            return 0.0
        mid = len(values) // 2
        if len(values) % 2 == 0:
            return (values[mid - 1] + values[mid]) / 2
        return values[mid]

    return {
        "median_success_time_seconds": round(median(success_times), 2),
        "median_damage_per_bed": round(median(damage_per_bed), 2),
    }


def compute_recent_window(
    db: Database,
    window_size: int,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float | int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT
            status,
            first_bed_seconds,
            success_time_seconds,
            major_hit_count,
            major_damage_total
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, window_size),
    )
    attempts = len(rows)
    successes = sum(1 for row in rows if row["status"] == "success")
    avg_first_bed = [
        _safe_float(row["first_bed_seconds"]) for row in rows if row["first_bed_seconds"] is not None
    ]
    avg_success_time = [
        _safe_float(row["success_time_seconds"])
        for row in rows
        if row["status"] == "success" and row["success_time_seconds"] is not None
    ]
    damage_values = []
    for row in rows:
        major_hit_count = _safe_int(row["major_hit_count"])
        if major_hit_count > 0:
            damage_values.append(_safe_float(row["major_damage_total"]) / major_hit_count)
    return {
        "window": window_size,
        "attempts": attempts,
        "success_rate": round(_pct(successes, attempts), 2),
        "avg_first_bed_seconds": round(sum(avg_first_bed) / len(avg_first_bed), 2) if avg_first_bed else 0.0,
        "avg_success_time_seconds": round(sum(avg_success_time) / len(avg_success_time), 2)
        if avg_success_time
        else 0.0,
        "avg_damage_per_bed": round(sum(damage_values) / len(damage_values), 2) if damage_values else 0.0,
    }


def compute_time_series(
    db: Database,
    limit: int = 200,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT
            id,
            started_at_utc,
            status,
            first_bed_seconds,
            success_time_seconds,
            major_hit_count,
            major_damage_total
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        major_hit_count = _safe_int(row["major_hit_count"])
        major_damage_total = _safe_int(row["major_damage_total"])
        damage_per_bed = (major_damage_total / major_hit_count) if major_hit_count > 0 else 0.0
        result.append(
            {
                "id": _safe_int(row["id"]),
                "started_at_utc": row["started_at_utc"],
                "status": row["status"],
                "first_bed_seconds": round(_safe_float(row["first_bed_seconds"]), 2),
                "success_time_seconds": round(_safe_float(row["success_time_seconds"]), 2),
                "damage_per_bed": round(damage_per_bed, 2),
            }
        )
    return result


def compute_rolling_consistency(
    db: Database,
    window_size: int = 10,
    limit: int = 400,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT id, status
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    rows = list(reversed(rows))
    window: list[int] = []
    successes = 0
    result: list[dict[str, Any]] = []
    for row in rows:
        is_success = 1 if row["status"] == "success" else 0
        window.append(is_success)
        successes += is_success
        if len(window) > window_size:
            successes -= window.pop(0)
        attempts_in_window = len(window)
        result.append(
            {
                "id": _safe_int(row["id"]),
                "window_attempts": attempts_in_window,
                "rolling_success_rate": round(_pct(successes, attempts_in_window), 2),
                "is_full_window": attempts_in_window == window_size,
            }
        )
    return result


def compute_time_between_attempts(
    db: Database,
    limit: int = 200,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT id, started_at_utc
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    timestamps = [row["started_at_utc"] for row in reversed(rows)]
    result: list[dict[str, Any]] = []
    prev = None
    for idx, ts in enumerate(timestamps):
        delta = 0.0
        if prev and ts:
            try:
                prev_dt = prev
                curr_dt = ts
                delta = (
                    __import__("datetime")
                    .datetime.fromisoformat(curr_dt)
                    .timestamp()
                    - __import__("datetime").datetime.fromisoformat(prev_dt).timestamp()
                )
            except ValueError:
                delta = 0.0
        result.append({"index": idx + 1, "seconds_between": round(delta, 2)})
        prev = ts
    return result


def compute_outcome_runs(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id ASC
        """,
        params,
    )
    runs: list[dict[str, Any]] = []
    if not rows:
        return {"runs": runs, "best_success_run": 0, "best_fail_run": 0}
    current_status = rows[0]["status"]
    current_len = 1
    best_success = 0
    best_fail = 0
    for row in rows[1:]:
        status = row["status"]
        if status == current_status:
            current_len += 1
        else:
            runs.append({"status": current_status, "length": current_len})
            if current_status == "success":
                best_success = max(best_success, current_len)
            else:
                best_fail = max(best_fail, current_len)
            current_status = status
            current_len = 1
    runs.append({"status": current_status, "length": current_len})
    if current_status == "success":
        best_success = max(best_success, current_len)
    else:
        best_fail = max(best_fail, current_len)
    return {"runs": runs, "best_success_run": best_success, "best_fail_run": best_fail}


def compute_speed_bins(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT success_time_seconds
        FROM attempts
        WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
        """,
        params,
    )
    bins = [
        {"label": "<28s", "min": 0, "max": 28, "count": 0},
        {"label": "28-30s", "min": 28, "max": 30, "count": 0},
        {"label": "30-32s", "min": 30, "max": 32, "count": 0},
        {"label": "32-35s", "min": 32, "max": 35, "count": 0},
        {"label": "35s+", "min": 35, "max": 10_000, "count": 0},
    ]
    for row in rows:
        value = _safe_float(row["success_time_seconds"])
        for bucket in bins:
            if bucket["min"] <= value < bucket["max"]:
                bucket["count"] += 1
                break
    return [{"label": b["label"], "count": b["count"]} for b in bins]


def compute_first_bed_bins(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_any = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT first_bed_seconds
        FROM attempts
        WHERE first_bed_seconds IS NOT NULL{where_any}
        """,
        params,
    )
    bins = [
        {"label": "<14s", "min": 0, "max": 14, "count": 0},
        {"label": "14-16s", "min": 14, "max": 16, "count": 0},
        {"label": "16-18s", "min": 16, "max": 18, "count": 0},
        {"label": "18-20s", "min": 18, "max": 20, "count": 0},
        {"label": "20s+", "min": 20, "max": 10_000, "count": 0},
    ]
    for row in rows:
        value = _safe_float(row["first_bed_seconds"])
        for bucket in bins:
            if bucket["min"] <= value < bucket["max"]:
                bucket["count"] += 1
                break
    return [{"label": b["label"], "count": b["count"]} for b in bins]


def compute_best_and_recent(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    best_success = db.query_one(
        f"""
        SELECT MIN(success_time_seconds) AS best_time
        FROM attempts
        WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
        """,
        params,
    )
    recent_success = db.query_one(
        f"""
        SELECT AVG(success_time_seconds) AS avg_time
        FROM (
            SELECT success_time_seconds
            FROM attempts
            WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
            ORDER BY id DESC
            LIMIT 20
        )
        """,
        params,
    )
    return {
        "best_success_time_seconds": round(_safe_float(best_success["best_time"] if best_success else 0.0), 2),
        "recent_success_time_seconds": round(
            _safe_float(recent_success["avg_time"] if recent_success else 0.0), 2
        ),
    }


def compute_attempts_by_session(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    sessions = compute_session_progression(
        db, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )
    return [
        {
            "session_label": row["session_label"],
            "attempts": row["attempts"],
            "successes": row["successes"],
            "success_rate": row["success_rate"],
        }
        for row in sessions
    ]


def compute_o_level_consistency(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            o_level,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL{where}
        GROUP BY o_level
        ORDER BY o_level ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "o_level": _safe_int(row["o_level"]),
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_o_level_heatmap(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
    *,
    default_min: int = 52,
    default_max: int = 59,
    hard_min: int = 45,
    hard_max: int = 70,
) -> dict[str, Any]:
    leniency_lookup, _ = _load_mpk_leniency_lookup()
    is_mpk_mode = ATTEMPT_SOURCE_CTX.get() == "mpk"
    locked_target_set = set(get_mpk_locked_targets(db)) if is_mpk_mode else set()
    if is_mpk_mode:
        _, map_levels, _ = _load_mpk_seed_map()
        if map_levels:
            default_min = min(_MPK_HEATMAP_MIN_LEVEL, min(map_levels))
            default_max = max(_MPK_HEATMAP_MAX_LEVEL, max(map_levels))
        else:
            default_min = _MPK_HEATMAP_MIN_LEVEL
            default_max = _MPK_HEATMAP_MAX_LEVEL
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            o_level,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where}
        GROUP BY COALESCE(tower_name, 'Unknown'), side, o_level
        ORDER BY side ASC, tower_name ASC, o_level ASC
        """,
        params,
    )
    standing_rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            o_level,
            standing_height,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL
          AND standing_height IS NOT NULL
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where}
        GROUP BY COALESCE(tower_name, 'Unknown'), side, o_level, standing_height
        ORDER BY side ASC, tower_name ASC, o_level ASC, standing_height ASC
        """,
        params,
    )
    expected_towers = [
        "Small Boy",
        "Small Cage",
        "Tall Cage",
        "M-85",
        "M-88",
        "M-91",
        "T-94",
        "T-97",
        "T-100",
        "Tall Boy",
    ]
    side_order = {"Front": 0, "Back": 1}
    tower_order = {name: idx for idx, name in enumerate(expected_towers)}

    def _allowed_combo(combo_tower: str, combo_side: str) -> bool:
        if combo_side not in {"Front", "Back"}:
            return False
        if tower_name is not None and combo_tower != tower_name:
            return False
        if front_back is not None and combo_side != front_back:
            return False
        return True

    by_combo_level: dict[tuple[str, str], dict[int, dict[str, int]]] = {}
    standing_by_combo_level: dict[tuple[str, str], dict[int, list[dict[str, int]]]] = {}
    min_seen: int | None = None
    max_seen: int | None = None
    for row in rows:
        combo_tower = str(row["tower_name"])
        combo_side = str(row["side"])
        if not _allowed_combo(combo_tower, combo_side):
            continue
        level = _safe_int(row["o_level"])
        if level in _HEATMAP_EXCLUDED_O_LEVELS:
            continue
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        combo_key = (combo_tower, combo_side)
        if combo_key not in by_combo_level:
            by_combo_level[combo_key] = {}
        by_combo_level[combo_key][level] = {"attempts": attempts, "successes": successes}
        min_seen = level if min_seen is None else min(min_seen, level)
        max_seen = level if max_seen is None else max(max_seen, level)
    for row in standing_rows:
        combo_tower = str(row["tower_name"])
        combo_side = str(row["side"])
        if not _allowed_combo(combo_tower, combo_side):
            continue
        level = _safe_int(row["o_level"])
        if level in _HEATMAP_EXCLUDED_O_LEVELS:
            continue
        standing = _safe_int(row["standing_height"])
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        combo_key = (combo_tower, combo_side)
        if combo_key not in standing_by_combo_level:
            standing_by_combo_level[combo_key] = {}
        if level not in standing_by_combo_level[combo_key]:
            standing_by_combo_level[combo_key][level] = []
        standing_by_combo_level[combo_key][level].append(
            {
                "standing_height": standing,
                "attempts": attempts,
                "successes": successes,
            }
        )

    if min_seen is None or max_seen is None:
        min_level = default_min
        max_level = default_max
    else:
        min_level = max(hard_min, min(default_min, min_seen))
        max_level = min(hard_max, max(default_max, max_seen))

    o_levels = [
        level
        for level in range(min_level, max_level + 1)
        if level not in _HEATMAP_EXCLUDED_O_LEVELS
    ]
    if is_mpk_mode and _MPK_HEATMAP_MIN_LEVEL not in o_levels:
        o_levels = sorted([_MPK_HEATMAP_MIN_LEVEL, *o_levels])

    combo_keys: set[tuple[str, str]] = set()
    for side in ("Front", "Back"):
        for expected_tower in expected_towers:
            if _allowed_combo(expected_tower, side):
                combo_keys.add((expected_tower, side))
    combo_keys.update(by_combo_level.keys())
    combo_keys = {combo for combo in combo_keys if _allowed_combo(combo[0], combo[1])}
    sorted_combos = sorted(
        combo_keys,
        key=lambda combo: (
            side_order.get(combo[1], 99),
            tower_order.get(combo[0], 999),
            combo[0],
        ),
    )

    matrix_rows: list[dict[str, Any]] = []
    for combo_tower, combo_side in sorted_combos:
        level_buckets = by_combo_level.get((combo_tower, combo_side), {})
        standing_buckets = standing_by_combo_level.get((combo_tower, combo_side), {})
        cells: list[dict[str, Any]] = []
        row_attempts = 0
        row_successes = 0
        for level in o_levels:
            bucket = level_buckets.get(level, {"attempts": 0, "successes": 0})
            attempts = int(bucket["attempts"])
            successes = int(bucket["successes"])
            row_attempts += attempts
            row_successes += successes
            leniency_value = leniency_lookup.get((combo_tower, combo_side, level))
            target_key = f"mpk|{combo_tower}|{combo_side}|{level}" if is_mpk_mode else None
            cells.append(
                {
                    "o_level": level,
                    "attempts": attempts,
                    "successes": successes,
                    "success_rate": round(_pct(successes, attempts), 2) if attempts > 0 else None,
                    "leniency": round(float(leniency_value), 2) if leniency_value is not None else None,
                    "standing_height_breakdown": standing_buckets.get(level, []),
                    "target_key": target_key,
                    "is_locked": bool(target_key in locked_target_set) if target_key is not None else False,
                }
            )
        matrix_rows.append(
            {
                "tower_name": combo_tower,
                "side": combo_side,
                "label": f"{combo_side} {combo_tower}",
                "attempts": row_attempts,
                "successes": row_successes,
                "success_rate": round(_pct(row_successes, row_attempts), 2)
                if row_attempts > 0
                else None,
                "cells": cells,
            }
        )

    return {
        "o_levels": o_levels,
        "rows": matrix_rows,
    }


def compute_standing_height_consistency(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            standing_height,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND standing_height IS NOT NULL{where}
        GROUP BY standing_height
        ORDER BY standing_height ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "standing_height": _safe_int(row["standing_height"]),
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_tower_front_back_overview(db: Database) -> list[dict[str, Any]]:
    where, params = _scope_where(include_where=True)
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back
        ORDER BY attempts DESC, tower_name ASC, front_back ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "attempts": _safe_int(row["attempts"]),
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_tower_radar(db: Database) -> dict[str, Any]:
    where_extra, params = _scope_where(include_where=False)
    # Keep a stable tower list so both Front/Back charts always show all known towers.
    all_towers_rows = db.query_all(
        f"""
        SELECT DISTINCT COALESCE(tower_name, 'Unknown') AS tower_name
        FROM attempts
        WHERE COALESCE(tower_name, 'Unknown') <> 'Unknown'{where_extra}
        ORDER BY tower_name ASC
        """,
        params,
    )
    all_towers = [str(r["tower_name"]) for r in all_towers_rows]

    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            status,
            explosives_used,
            explosives_left
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where_extra}
        """,
        params,
    )

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        side = str(row["front_back"])
        if side not in {"Front", "Back"}:
            continue
        tower = str(row["tower_name"])
        key = (tower, side)
        bucket = grouped.setdefault(
            key,
            {
                "attempt_count": 0,
                "success_count": 0,
                "explosives_success": [],
            },
        )
        bucket["attempt_count"] += 1
        if row["status"] == "success":
            bucket["success_count"] += 1
            used = row["explosives_used"]
            if used is not None:
                total = int(used)
                left = row["explosives_left"]
                if left is not None:
                    total += int(left)
                bucket["explosives_success"].append(total)

    def median(values: list[int]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 0:
            return (ordered[mid - 1] + ordered[mid]) / 2.0
        return float(ordered[mid])

    by_side: dict[str, list[dict[str, Any]]] = {"Front": [], "Back": []}
    for side in ("Front", "Back"):
        for tower in all_towers:
            bucket = grouped.get((tower, side), None)
            attempts = int(bucket["attempt_count"]) if bucket else 0
            successes = int(bucket["success_count"]) if bucket else 0
            success_rate = round(_pct(successes, attempts), 2)
            median_expl = round(median(bucket["explosives_success"]) if bucket else 0.0, 2)
            by_side[side].append(
                {
                    "tower_name": tower,
                    "attempt_count": attempts,
                    "success_rate": min(100.0, max(0.0, success_rate)),
                    "median_explosives": min(8.0, max(0.0, median_expl)),
                }
            )

    front_attempt_max = max((row["attempt_count"] for row in by_side["Front"]), default=0)
    back_attempt_max = max((row["attempt_count"] for row in by_side["Back"]), default=0)
    front_attempt_cap = max(1, front_attempt_max)
    back_attempt_cap = max(1, back_attempt_max)

    # Apply requested cap: attempt count is capped at highest attempt count in that side.
    for row in by_side["Front"]:
        row["attempt_count_capped"] = min(front_attempt_cap, row["attempt_count"])
    for row in by_side["Back"]:
        row["attempt_count_capped"] = min(back_attempt_cap, row["attempt_count"])

    return {
        "front": by_side["Front"],
        "back": by_side["Back"],
        "front_attempt_cap": front_attempt_cap,
        "back_attempt_cap": back_attempt_cap,
    }


def build_dashboard_payload(db: Database) -> dict[str, Any]:
    def scoped(tower_name: str | None = None, front_back: str | None = None) -> dict[str, Any]:
        return {
            "summary": compute_summary(db, tower_name=tower_name, front_back=front_back),
            "streaks": compute_streaks(db, tower_name=tower_name, front_back=front_back),
            "bests": compute_best_and_recent(db, tower_name=tower_name, front_back=front_back),
            "consistency_windows": [
                compute_window_consistency(db, 10, tower_name=tower_name, front_back=front_back),
                compute_window_consistency(db, 25, tower_name=tower_name, front_back=front_back),
                compute_window_consistency(db, 50, tower_name=tower_name, front_back=front_back),
            ],
            "damage_per_bed": compute_damage_per_bed(db, tower_name=tower_name, front_back=front_back),
            "tower_performance": compute_tower_performance(
                db, tower_name=tower_name, front_back=front_back
            ),
            "tower_type_breakdown": compute_tower_type_breakdown(
                db, tower_name=tower_name, front_back=front_back
            ),
            "session_progression": compute_session_progression(
                db, tower_name=tower_name, front_back=front_back
            ),
            "time_series": compute_time_series(
                db, limit=200, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_10": compute_rolling_consistency(
                db, window_size=10, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_25": compute_rolling_consistency(
                db, window_size=25, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_50": compute_rolling_consistency(
                db, window_size=50, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "outcome_runs": compute_outcome_runs(db, tower_name=tower_name, front_back=front_back),
            "speed_bins": compute_speed_bins(db, tower_name=tower_name, front_back=front_back),
            "attempts_by_session": compute_attempts_by_session(
                db, tower_name=tower_name, front_back=front_back
            ),
            "recent_attempts": compute_recent_attempts(
                db, limit=60, tower_name=tower_name, front_back=front_back
            ),
        }
    def build_scoped_payload_for_window(window_key: str, bounds: dict[str, Any]) -> dict[str, Any]:
        tok_id = WINDOW_MIN_ID_CTX.set(bounds.get("min_id"))
        tok_start = WINDOW_START_UTC_CTX.set(bounds.get("start_utc"))
        try:
            tower_front_back_overview = compute_tower_front_back_overview(db)
            available_towers = sorted(
                {
                    row["tower_name"]
                    for row in tower_front_back_overview
                    if row["tower_name"] not in {"", "Unknown"}
                }
            )
            available_front_backs = sorted(
                {
                    row["front_back"]
                    for row in tower_front_back_overview
                    if row["front_back"] in {"Front", "Back"}
                }
            )

            by_tower: dict[str, Any] = {}
            for tower_name in available_towers:
                by_tower[tower_name] = scoped(tower_name=tower_name)

            by_front_back: dict[str, Any] = {}
            for front_back in available_front_backs:
                by_front_back[front_back] = scoped(front_back=front_back)

            by_tower_front_back: dict[str, Any] = {}
            for tower_name in available_towers:
                for front_back in available_front_backs:
                    by_tower_front_back[f"{tower_name}|{front_back}"] = scoped(
                        tower_name=tower_name, front_back=front_back
                    )

            return {
                "global": scoped(None),
                "tower_front_back_overview": tower_front_back_overview,
                "tower_radar": compute_tower_radar(db),
                "available_towers": available_towers,
                "available_front_backs": available_front_backs,
                "by_tower": by_tower,
                "by_front_back": by_front_back,
                "by_tower_front_back": by_tower_front_back,
                "window_key": window_key,
            }
        finally:
            WINDOW_MIN_ID_CTX.reset(tok_id)
            WINDOW_START_UTC_CTX.reset(tok_start)

    def build_mode_payload(include_straight: bool, rotation_filter: str) -> dict[str, Any]:
        tok_straight = INCLUDE_STRAIGHT_CTX.set(include_straight)
        tok_rotation = ROTATION_FILTER_CTX.set(rotation_filter)
        try:
            window_order = ["all", "current_session", "last_10", "last_25", "last_50", "last_100"]
            window_labels = {
                "all": "All",
                "current_session": "Current Session",
                "last_10": "Last 10",
                "last_25": "Last 25",
                "last_50": "Last 50",
                "last_100": "Last 100",
            }
            bounds_by_window = _compute_window_bounds(db)
            windows_payload: dict[str, Any] = {}
            for key in window_order:
                windows_payload[key] = build_scoped_payload_for_window(key, bounds_by_window.get(key, {}))

            all_payload = windows_payload["all"]
            return {
                **all_payload,
                "windows": windows_payload,
                "window_options": [{"key": key, "label": window_labels[key]} for key in window_order],
                "default_window": "all",
            }
        finally:
            ROTATION_FILTER_CTX.reset(tok_rotation)
            INCLUDE_STRAIGHT_CTX.reset(tok_straight)

    mode_payloads: dict[str, Any] = {}
    for mode_key, include_straight in (("exclude_1_8", False), ("include_1_8", True)):
        rotation_modes: dict[str, Any] = {}
        for rotation_key in ("both", "cw", "ccw"):
            rotation_modes[rotation_key] = build_mode_payload(include_straight, rotation_key)
        mode_payloads[mode_key] = {
            "rotation_modes": rotation_modes,
            "default_rotation": "both",
        }

    default_payload = mode_payloads["exclude_1_8"]["rotation_modes"]["both"]
    practice_next = compute_practice_next_widget(db)
    return {
        **default_payload,
        "modes": mode_payloads,
        "default_include_1_8": False,
        "default_rotation": "both",
        "practice_next": practice_next,
    }
