from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from collections import deque
from typing import Any

import nbtlib
try:
    import anvil  # type: ignore
except Exception:  # pragma: no cover - optional at runtime
    anvil = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parse_command_storage import run_metrics_from_storage, world_seed_from_level_dat
from config import DB_PATH
from render_stronghold_map_svg import render_map_svg

_F3C_TP_RE = re.compile(
    r"^/execute\s+in\s+(?P<dim>[a-z0-9_:\.-]+)\s+run\s+tp\s+@s\s+"
    r"(?P<x>-?\d+(?:\.\d+)?)\s+"
    r"(?P<y>-?\d+(?:\.\d+)?)\s+"
    r"(?P<z>-?\d+(?:\.\d+)?)\s+"
    r"(?P<yaw>-?\d+(?:\.\d+)?)\s+"
    r"(?P<pitch>-?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def _find_storage_file(data_dir: Path) -> Path | None:
    preferred = data_dir / "command_storage_zdash.dat"
    if preferred.exists():
        return preferred
    candidates = sorted(
        data_dir.glob("command_storage_*.dat"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _write_samples_csv(path: Path, samples: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["gt", "x", "y", "z", "dim"])
        for sample in samples:
            writer.writerow(
                [
                    int(sample.get("gt", 0) or 0),
                    int(sample.get("x", 0) or 0),
                    int(sample.get("y", 0) or 0),
                    int(sample.get("z", 0) or 0),
                    int(sample.get("dim", 0) or 0),
                ]
            )


def _safe_filename(text: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid else ch for ch in str(text))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "world"


def _world_output_stem(world_name: str) -> str:
    match = re.search(r"#(\d+)", str(world_name))
    if match:
        return match.group(1)
    return _safe_filename(world_name).replace(" ", "_")


def _default_outputs_for_world(world_dir: Path) -> tuple[Path, Path]:
    base_dir = ROOT_DIR / "data" / "stronghold_maps"
    stem = _world_output_stem(world_dir.name)
    out_json = base_dir / f"{stem}.json"
    samples_csv = base_dir / f"{stem}_samples.csv"
    return out_json, samples_csv


def _nbt_int(value: Any, default: int = 0) -> int:
    try:
        return int(getattr(value, "value", value))
    except Exception:
        return int(default)


def _nbt_str(value: Any, default: str = "") -> str:
    try:
        return str(getattr(value, "value", value))
    except Exception:
        return str(default)


def _map_bounds_from_payload(payload: dict[str, Any]) -> tuple[int, int, int, int, int, int] | None:
    pieces = payload.get("pieces", [])
    if not isinstance(pieces, list) or not pieces:
        return None
    min_x = None
    max_x = None
    min_y = None
    max_y = None
    min_z = None
    max_z = None
    for room in pieces:
        if not isinstance(room, dict):
            continue
        try:
            rx0 = int(room.get("min_x"))
            rx1 = int(room.get("max_x"))
            ry0 = int(room.get("min_y"))
            ry1 = int(room.get("max_y"))
            rz0 = int(room.get("min_z"))
            rz1 = int(room.get("max_z"))
        except Exception:
            continue
        min_x = rx0 if min_x is None else min(min_x, rx0)
        max_x = rx1 if max_x is None else max(max_x, rx1)
        min_y = ry0 if min_y is None else min(min_y, ry0)
        max_y = ry1 if max_y is None else max(max_y, ry1)
        min_z = rz0 if min_z is None else min(min_z, rz0)
        max_z = rz1 if max_z is None else max(max_z, rz1)
    if None in (min_x, max_x, min_y, max_y, min_z, max_z):
        return None
    return (int(min_x), int(max_x), int(min_y), int(max_y), int(min_z), int(max_z))


def _door_scan_bounds_from_payload(payload: dict[str, Any]) -> tuple[int, int, int, int] | None:
    bounds_3d = _map_bounds_from_payload(payload)
    px0 = None
    px1 = None
    pz0 = None
    pz1 = None
    for point in payload.get("path", []):
        if not isinstance(point, dict):
            continue
        if int(point.get("dim", 0) or 0) != 0:
            continue
        try:
            x = int(float(point.get("x")))
            z = int(float(point.get("z")))
        except Exception:
            continue
        px0 = x if px0 is None else min(px0, x)
        px1 = x if px1 is None else max(px1, x)
        pz0 = z if pz0 is None else min(pz0, z)
        pz1 = z if pz1 is None else max(pz1, z)

    if bounds_3d is None and None in (px0, px1, pz0, pz1):
        return None

    if bounds_3d is None:
        x0, x1, z0, z1 = int(px0), int(px1), int(pz0), int(pz1)
    elif None in (px0, px1, pz0, pz1):
        min_x, max_x, _min_y, _max_y, min_z, max_z = bounds_3d
        x0, x1, z0, z1 = min_x, max_x, min_z, max_z
    else:
        min_x, max_x, _min_y, _max_y, min_z, max_z = bounds_3d
        x0 = min(min_x, int(px0))
        x1 = max(max_x, int(px1))
        z0 = min(min_z, int(pz0))
        z1 = max(max_z, int(pz1))

    margin = 16
    return (x0 - margin, x1 + margin, z0 - margin, z1 + margin)


def _scan_world_doors(world_dir: Path, bounds_2d: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    if anvil is None:
        return []
    region_dir = world_dir / "region"
    if not region_dir.exists():
        return []
    x0, x1, z0, z1 = bounds_2d
    # Stronghold Y from generator bounds can be offset from real world Y.
    # Scan full useful vertical range and trust x/z window from tracker + pieces.
    y0 = 0
    y1 = 127

    cx0 = x0 // 16
    cx1 = x1 // 16
    cz0 = z0 // 16
    cz1 = z1 // 16
    region_cache: dict[tuple[int, int], Any] = {}
    seen: dict[tuple[int, int, str], tuple[int, str]] = {}

    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            rx = cx // 32
            rz = cz // 32
            rkey = (rx, rz)
            region = region_cache.get(rkey)
            if region is None:
                rpath = region_dir / f"r.{rx}.{rz}.mca"
                if not rpath.exists():
                    region_cache[rkey] = False
                    continue
                try:
                    region = anvil.Region.from_file(str(rpath))
                except Exception:
                    region_cache[rkey] = False
                    continue
                region_cache[rkey] = region
            if region is False:
                continue
            local_cx = cx - (rx * 32)
            local_cz = cz - (rz * 32)
            try:
                chunk = region.get_chunk(local_cx, local_cz)
            except Exception:
                continue
            for lx in range(16):
                gx = cx * 16 + lx
                if gx < x0 or gx > x1:
                    continue
                for lz in range(16):
                    gz = cz * 16 + lz
                    if gz < z0 or gz > z1:
                        continue
                    for y in range(y0, y1 + 1):
                        try:
                            block = chunk.get_block(lx, y, lz)
                        except Exception:
                            continue
                        bid = str(getattr(block, "id", None) or getattr(block, "name", None) or block)
                        if "oak_door" not in bid and "iron_door" not in bid:
                            continue
                        props = getattr(block, "properties", {}) or {}
                        half = str(props.get("half", "")).lower()
                        if half and half != "lower":
                            continue
                        facing = str(props.get("facing", "unknown")).lower()
                        door_type = "iron" if "iron_door" in bid else "oak"
                        key = (gx, gz, door_type)
                        prev = seen.get(key)
                        if prev is None or y < int(prev[0]):
                            seen[key] = (int(y), facing)

    doors: list[dict[str, Any]] = []
    for (x, z, door_type), (y, facing) in sorted(seen.items(), key=lambda item: (item[1][0], item[0][0], item[0][1])):
        doors.append({"x": int(x), "y": int(y), "z": int(z), "type": door_type, "facing": facing})
    return doors


def _assign_room_for_block(
    x: int,
    y: int,
    z: int,
    rooms: list[dict[str, Any]],
    *,
    y_shift: int,
) -> tuple[int, str]:
    for room in rooms:
        if x < int(room["min_x"]) or x > int(room["max_x"]):
            continue
        if z < int(room["min_z"]) or z > int(room["max_z"]):
            continue
        y0 = int(room.get("min_y", 0)) + y_shift - 1
        y1 = int(room.get("max_y", 0)) + y_shift + 1
        if y >= y0 and y <= y1:
            return (int(room["id"]), str(room["type"]))
    for room in rooms:
        if x >= int(room["min_x"]) and x <= int(room["max_x"]) and z >= int(room["min_z"]) and z <= int(room["max_z"]):
            return (int(room["id"]), str(room["type"]))
    return (-1, "Unknown")


class _WorldBlockLookup:
    def __init__(self, world_dir: Path) -> None:
        self.region_dir = world_dir / "region"
        self._region_cache: dict[tuple[int, int], Any] = {}
        self._chunk_cache: dict[tuple[int, int], Any] = {}

    def _get_region(self, rx: int, rz: int) -> Any | None:
        key = (rx, rz)
        cached = self._region_cache.get(key)
        if cached is not None:
            return None if cached is False else cached
        rpath = self.region_dir / f"r.{rx}.{rz}.mca"
        if not rpath.exists():
            self._region_cache[key] = False
            return None
        try:
            region = anvil.Region.from_file(str(rpath))
        except Exception:
            self._region_cache[key] = False
            return None
        self._region_cache[key] = region
        return region

    def _get_chunk(self, cx: int, cz: int) -> Any | None:
        key = (cx, cz)
        cached = self._chunk_cache.get(key)
        if cached is not None:
            return None if cached is False else cached
        rx = cx // 32
        rz = cz // 32
        region = self._get_region(rx, rz)
        if region is None:
            self._chunk_cache[key] = False
            return None
        local_cx = cx - (rx * 32)
        local_cz = cz - (rz * 32)
        try:
            chunk = region.get_chunk(local_cx, local_cz)
        except Exception:
            self._chunk_cache[key] = False
            return None
        self._chunk_cache[key] = chunk
        return chunk

    def block_id(self, x: int, y: int, z: int) -> str:
        if y < 0 or y > 255:
            return ""
        cx = x // 16
        cz = z // 16
        chunk = self._get_chunk(cx, cz)
        if chunk is None:
            return ""
        lx = x - (cx * 16)
        lz = z - (cz * 16)
        try:
            block = chunk.get_block(lx, y, lz)
        except Exception:
            return ""
        return str(getattr(block, "id", None) or block).lower()


def _payload_rooms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = []
    for piece in payload.get("pieces", []):
        if not isinstance(piece, dict):
            continue
        try:
            room = {
                "id": int(piece.get("id", -1)),
                "type": str(piece.get("type", "Unknown")),
                "facing": str(piece.get("facing", "NONE")),
                "min_x": int(piece.get("min_x")),
                "max_x": int(piece.get("max_x")),
                "min_y": int(piece.get("min_y")),
                "max_y": int(piece.get("max_y")),
                "min_z": int(piece.get("min_z")),
                "max_z": int(piece.get("max_z")),
            }
        except Exception:
            continue
        if room["id"] < 0:
            continue
        rooms.append(room)
    return rooms


def _estimate_room_y_shift(payload: dict[str, Any], rooms: list[dict[str, Any]]) -> int:
    if not rooms:
        return 0
    by_id: dict[int, dict[str, Any]] = {int(r["id"]): r for r in rooms}
    deltas: list[float] = []
    for point in payload.get("path", []):
        if not isinstance(point, dict):
            continue
        if int(point.get("dim", 0) or 0) != 0:
            continue
        rid = int(point.get("room_id", -1) or -1)
        room = by_id.get(rid)
        if room is None:
            continue
        try:
            y = float(point.get("y"))
        except Exception:
            continue
        rmin = float(room.get("min_y", 0))
        rmax = float(room.get("max_y", 0))
        if y < rmin:
            deltas.append(y - rmin)
        elif y > rmax:
            deltas.append(y - rmax)
        else:
            deltas.append(0.0)
    if not deltas:
        return 0
    deltas.sort()
    return int(round(deltas[len(deltas) // 2]))


def _room_scan_windows(payload: dict[str, Any], rooms: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    global_shift = _estimate_room_y_shift(payload, rooms)
    room_y_samples: dict[int, list[float]] = {}
    for point in payload.get("path", []):
        if not isinstance(point, dict):
            continue
        if int(point.get("dim", 0) or 0) != 0:
            continue
        rid = int(point.get("room_id", -1) or -1)
        if rid < 0:
            continue
        try:
            py = float(point.get("y"))
        except Exception:
            continue
        room_y_samples.setdefault(rid, []).append(py)

    windows: dict[int, tuple[int, int]] = {}
    for room in rooms:
        rid = int(room["id"])
        min_y = int(room.get("min_y", 0))
        max_y = int(room.get("max_y", 0))
        samples = sorted(room_y_samples.get(rid, []))
        if samples:
            mid = float(samples[len(samples) // 2])
            from_samples_0 = int(round(mid)) - 3
            from_samples_1 = int(round(mid)) + 3
        else:
            center = (float(min_y) + float(max_y)) * 0.5 + float(global_shift)
            from_samples_0 = int(round(center)) - 3
            from_samples_1 = int(round(center)) + 3
        shifted_0 = min_y + global_shift - 3
        shifted_1 = max_y + global_shift + 3
        y0 = max(1, min(from_samples_0, shifted_0))
        y1 = min(127, max(from_samples_1, shifted_1))
        if y1 <= y0:
            y1 = min(127, y0 + 2)
        windows[rid] = (int(y0), int(y1))
    return windows


def _scan_room_masks(world_dir: Path, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, tuple[int, int]]]:
    if anvil is None:
        return ([], {})
    rooms = _payload_rooms(payload)
    if not rooms:
        return ([], {})
    lookup = _WorldBlockLookup(world_dir)
    windows = _room_scan_windows(payload, rooms)
    passable = {"air", "cave_air", "void_air", "oak_door", "iron_door", "iron_bars"}

    def _is_passable(bid: str) -> bool:
        return bid in passable

    room_masks: list[dict[str, Any]] = []
    for room in rooms:
        rid = int(room["id"])
        y0, y1 = windows.get(rid, (1, 127))
        rx0 = int(room["min_x"])
        rx1 = int(room["max_x"])
        rz0 = int(room["min_z"])
        rz1 = int(room["max_z"])
        z_to_xs: dict[int, list[int]] = {}

        for z in range(rz0, rz1 + 1):
            for x in range(rx0, rx1 + 1):
                walkable = False
                for y in range(y0, y1):
                    b0 = lookup.block_id(x, y, z)
                    b1 = lookup.block_id(x, y + 1, z)
                    below = lookup.block_id(x, y - 1, z)
                    if _is_passable(b0) and _is_passable(b1) and not _is_passable(below):
                        walkable = True
                        break
                if walkable:
                    z_to_xs.setdefault(z, []).append(x)

        runs: list[dict[str, int]] = []
        for z, xs in sorted(z_to_xs.items(), key=lambda item: int(item[0])):
            xs_sorted = sorted(set(xs))
            if not xs_sorted:
                continue
            run_start = xs_sorted[0]
            prev = xs_sorted[0]
            for x in xs_sorted[1:]:
                if x == prev + 1:
                    prev = x
                    continue
                runs.append({"z": int(z), "x0": int(run_start), "x1": int(prev)})
                run_start = x
                prev = x
            runs.append({"z": int(z), "x0": int(run_start), "x1": int(prev)})

        fallback_bbox = False
        if not runs:
            fallback_bbox = True
            for z in range(rz0, rz1 + 1):
                runs.append({"z": int(z), "x0": int(rx0), "x1": int(rx1)})

        room_masks.append(
            {
                "room_id": rid,
                "room_type": str(room.get("type", "Unknown")),
                "facing": str(room.get("facing", "NONE")),
                "runs": runs,
                "fallback_bbox": bool(fallback_bbox),
                "scan_y0": int(y0),
                "scan_y1": int(y1),
            }
        )

    room_masks.sort(key=lambda item: int(item.get("room_id", -1)))
    return (room_masks, windows)


def _scan_connection_polygons(
    world_dir: Path,
    payload: dict[str, Any],
    windows: dict[int, tuple[int, int]],
) -> list[dict[str, Any]]:
    if anvil is None:
        return []
    rooms = _payload_rooms(payload)
    if not rooms:
        return []
    lookup = _WorldBlockLookup(world_dir)
    passable = {"air", "cave_air", "void_air", "oak_door", "iron_door", "iron_bars"}
    door_ids = {"oak_door", "iron_door"}

    def _is_passable(bid: str) -> bool:
        return bid in passable

    out: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int, str]] = set()
    bridge_thickness = 0.92

    for i in range(len(rooms)):
        a = rooms[i]
        a_id = int(a["id"])
        for j in range(i + 1, len(rooms)):
            b = rooms[j]
            b_id = int(b["id"])
            z_overlap0 = max(int(a["min_z"]), int(b["min_z"]))
            z_overlap1 = min(int(a["max_z"]), int(b["max_z"]))
            x_overlap0 = max(int(a["min_x"]), int(b["min_x"]))
            x_overlap1 = min(int(a["max_x"]), int(b["max_x"]))
            ay0, ay1 = windows.get(a_id, (1, 127))
            by0, by1 = windows.get(b_id, (1, 127))
            y0 = max(1, min(ay0, by0))
            y1 = min(127, max(ay1, by1))
            if y1 <= y0:
                continue

            # Shared X face (rooms left/right of each other).
            if z_overlap1 - z_overlap0 >= 1:
                x_gap_1 = abs(int(a["max_x"]) - int(b["min_x"]))
                x_gap_2 = abs(int(b["max_x"]) - int(a["min_x"]))
                if x_gap_1 <= 1 or x_gap_2 <= 1:
                    if int(a["max_x"]) <= int(b["min_x"]):
                        x_a = int(a["max_x"])
                        x_b = int(b["min_x"])
                    else:
                        x_a = int(b["max_x"])
                        x_b = int(a["min_x"])
                    key = (min(a_id, b_id), max(a_id, b_id), "x")
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        open_rows: list[tuple[int, bool]] = []
                        for z in range(z_overlap0, z_overlap1 + 1):
                            row_open = False
                            row_door = False
                            for y in range(y0, y1):
                                a0 = lookup.block_id(x_a, y, z)
                                a1 = lookup.block_id(x_a, y + 1, z)
                                b0 = lookup.block_id(x_b, y, z)
                                b1 = lookup.block_id(x_b, y + 1, z)
                                if _is_passable(a0) and _is_passable(a1) and _is_passable(b0) and _is_passable(b1):
                                    row_open = True
                                    if a0 in door_ids or a1 in door_ids or b0 in door_ids or b1 in door_ids:
                                        row_door = True
                                    break
                            if row_open:
                                open_rows.append((z, row_door))
                        if open_rows:
                            run_start = int(open_rows[0][0])
                            run_end = run_start
                            run_has_door = bool(open_rows[0][1])
                            for z, has_door in open_rows[1:]:
                                z = int(z)
                                if z == run_end + 1:
                                    run_end = z
                                    run_has_door = run_has_door or bool(has_door)
                                else:
                                    x_mid = (float(x_a) + float(x_b)) * 0.5
                                    out.append(
                                        {
                                            "a": int(a_id),
                                            "b": int(b_id),
                                            "axis": "x",
                                            "kind": "door" if run_has_door else "open",
                                            "x0": float(x_mid - bridge_thickness * 0.5),
                                            "x1": float(x_mid + bridge_thickness * 0.5),
                                            "z0": float(run_start),
                                            "z1": float(run_end + 1),
                                            "y": int((y0 + y1) // 2),
                                        }
                                    )
                                    run_start = z
                                    run_end = z
                                    run_has_door = bool(has_door)
                            x_mid = (float(x_a) + float(x_b)) * 0.5
                            out.append(
                                {
                                    "a": int(a_id),
                                    "b": int(b_id),
                                    "axis": "x",
                                    "kind": "door" if run_has_door else "open",
                                    "x0": float(x_mid - bridge_thickness * 0.5),
                                    "x1": float(x_mid + bridge_thickness * 0.5),
                                    "z0": float(run_start),
                                    "z1": float(run_end + 1),
                                    "y": int((y0 + y1) // 2),
                                }
                            )

            # Shared Z face (rooms above/below each other).
            if x_overlap1 - x_overlap0 >= 1:
                z_gap_1 = abs(int(a["max_z"]) - int(b["min_z"]))
                z_gap_2 = abs(int(b["max_z"]) - int(a["min_z"]))
                if z_gap_1 <= 1 or z_gap_2 <= 1:
                    if int(a["max_z"]) <= int(b["min_z"]):
                        z_a = int(a["max_z"])
                        z_b = int(b["min_z"])
                    else:
                        z_a = int(b["max_z"])
                        z_b = int(a["min_z"])
                    key = (min(a_id, b_id), max(a_id, b_id), "z")
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        open_cols: list[tuple[int, bool]] = []
                        for x in range(x_overlap0, x_overlap1 + 1):
                            col_open = False
                            col_door = False
                            for y in range(y0, y1):
                                a0 = lookup.block_id(x, y, z_a)
                                a1 = lookup.block_id(x, y + 1, z_a)
                                b0 = lookup.block_id(x, y, z_b)
                                b1 = lookup.block_id(x, y + 1, z_b)
                                if _is_passable(a0) and _is_passable(a1) and _is_passable(b0) and _is_passable(b1):
                                    col_open = True
                                    if a0 in door_ids or a1 in door_ids or b0 in door_ids or b1 in door_ids:
                                        col_door = True
                                    break
                            if col_open:
                                open_cols.append((x, col_door))
                        if open_cols:
                            run_start = int(open_cols[0][0])
                            run_end = run_start
                            run_has_door = bool(open_cols[0][1])
                            for x, has_door in open_cols[1:]:
                                x = int(x)
                                if x == run_end + 1:
                                    run_end = x
                                    run_has_door = run_has_door or bool(has_door)
                                else:
                                    z_mid = (float(z_a) + float(z_b)) * 0.5
                                    out.append(
                                        {
                                            "a": int(a_id),
                                            "b": int(b_id),
                                            "axis": "z",
                                            "kind": "door" if run_has_door else "open",
                                            "x0": float(run_start),
                                            "x1": float(run_end + 1),
                                            "z0": float(z_mid - bridge_thickness * 0.5),
                                            "z1": float(z_mid + bridge_thickness * 0.5),
                                            "y": int((y0 + y1) // 2),
                                        }
                                    )
                                    run_start = x
                                    run_end = x
                                    run_has_door = bool(has_door)
                            z_mid = (float(z_a) + float(z_b)) * 0.5
                            out.append(
                                {
                                    "a": int(a_id),
                                    "b": int(b_id),
                                    "axis": "z",
                                    "kind": "door" if run_has_door else "open",
                                    "x0": float(run_start),
                                    "x1": float(run_end + 1),
                                    "z0": float(z_mid - bridge_thickness * 0.5),
                                    "z1": float(z_mid + bridge_thickness * 0.5),
                                    "y": int((y0 + y1) // 2),
                                }
                            )

    out.sort(key=lambda item: (int(item.get("a", -1)), int(item.get("b", -1)), str(item.get("axis", "")), float(item.get("x0", 0.0)), float(item.get("z0", 0.0))))
    return out


def _connections_from_polygons(connection_polygons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    connections: list[dict[str, Any]] = []
    for poly in connection_polygons:
        try:
            x0 = float(poly.get("x0"))
            x1 = float(poly.get("x1"))
            z0 = float(poly.get("z0"))
            z1 = float(poly.get("z1"))
        except Exception:
            continue
        axis = str(poly.get("axis", ""))
        connections.append(
            {
                "a": int(poly.get("a", -1) or -1),
                "b": int(poly.get("b", -1) or -1),
                "axis": axis,
                "kind": str(poly.get("kind", "open")),
                "x": float((x0 + x1) * 0.5),
                "z": float((z0 + z1) * 0.5),
                "y": int(poly.get("y", 0) or 0),
            }
        )
    connections.sort(key=lambda c: (int(c.get("a", -1)), int(c.get("b", -1)), str(c.get("axis", "")), float(c.get("x", 0.0)), float(c.get("z", 0.0))))
    return connections


def _compute_optimal_nav(payload: dict[str, Any]) -> dict[str, Any]:
    pieces = payload.get("pieces", [])
    if not isinstance(pieces, list):
        pieces = []
    room_type_by_id: dict[int, str] = {}
    room_ids: set[int] = set()
    for piece in pieces:
        if not isinstance(piece, dict):
            continue
        rid = int(piece.get("id", -1) or -1)
        if rid < 0:
            continue
        room_ids.add(rid)
        room_type_by_id[rid] = str(piece.get("type", "Unknown"))

    starter_room_id = int(payload.get("starter", {}).get("room_id", -1) or -1)
    if starter_room_id < 0:
        visits = payload.get("visits", [])
        if isinstance(visits, list) and visits:
            for v in visits:
                if isinstance(v, dict):
                    rid = int(v.get("room_id", -1) or -1)
                    if rid >= 0:
                        starter_room_id = rid
                        break

    portal_room_ids = sorted([rid for rid, t in room_type_by_id.items() if t == "PortalRoom"])
    adjacency: dict[int, set[int]] = {rid: set() for rid in room_ids}
    for conn in payload.get("connections", []):
        if not isinstance(conn, dict):
            continue
        a = int(conn.get("a", -1) or -1)
        b = int(conn.get("b", -1) or -1)
        if a < 0 or b < 0 or a == b:
            continue
        if a not in adjacency:
            adjacency[a] = set()
        if b not in adjacency:
            adjacency[b] = set()
        adjacency[a].add(b)
        adjacency[b].add(a)

    out: dict[str, Any] = {
        "starter_room_id": int(starter_room_id),
        "portal_room_ids": portal_room_ids,
        "reachable": False,
        "min_edges": -1,
        "min_rooms": -1,
        "path_room_ids": [],
    }
    if starter_room_id < 0 or not portal_room_ids:
        return out
    if starter_room_id not in adjacency:
        adjacency[starter_room_id] = set()
    targets = set(portal_room_ids)

    q: deque[int] = deque([starter_room_id])
    dist: dict[int, int] = {starter_room_id: 0}
    prev: dict[int, int] = {}
    hit_target = -1
    while q:
        cur = q.popleft()
        if cur in targets:
            hit_target = cur
            break
        for nxt in sorted(adjacency.get(cur, set())):
            if nxt in dist:
                continue
            dist[nxt] = dist[cur] + 1
            prev[nxt] = cur
            q.append(nxt)

    if hit_target < 0:
        return out
    path_ids: list[int] = [hit_target]
    while path_ids[-1] != starter_room_id:
        parent = prev.get(path_ids[-1])
        if parent is None:
            break
        path_ids.append(parent)
    path_ids.reverse()

    min_edges = int(dist.get(hit_target, max(0, len(path_ids) - 1)))
    min_rooms = int(min_edges + 1) if min_edges >= 0 else -1
    out["reachable"] = True
    out["target_portal_room_id"] = int(hit_target)
    out["min_edges"] = min_edges
    out["min_rooms"] = min_rooms
    out["path_room_ids"] = path_ids
    return out


def _scan_world_connections(world_dir: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if anvil is None:
        return []
    rooms = _payload_rooms(payload)
    if not rooms:
        return []

    lookup = _WorldBlockLookup(world_dir)
    passable = {"air", "cave_air", "void_air", "oak_door", "iron_door", "iron_bars"}
    door_ids = {"oak_door", "iron_door"}
    y_shift = _estimate_room_y_shift(payload, rooms)
    y_min_global = 0
    y_max_global = 127

    def _is_passable(bid: str) -> bool:
        return bid in passable

    def _scan_face_x(x_a: int, x_b: int, z0: int, z1: int, y0: int, y1: int) -> tuple[int, int, bool] | None:
        hits: list[tuple[int, int]] = []
        saw_door = False
        for z in range(z0, z1 + 1):
            for y in range(y0, y1):
                a0 = lookup.block_id(x_a, y, z)
                a1 = lookup.block_id(x_a, y + 1, z)
                b0 = lookup.block_id(x_b, y, z)
                b1 = lookup.block_id(x_b, y + 1, z)
                if _is_passable(a0) and _is_passable(a1) and _is_passable(b0) and _is_passable(b1):
                    hits.append((z, y))
                    if a0 in door_ids or b0 in door_ids or a1 in door_ids or b1 in door_ids:
                        saw_door = True
                    break
        if len(hits) < 2:
            return None
        mid = hits[len(hits) // 2]
        return (int(mid[0]), int(mid[1]), saw_door)

    def _scan_face_z(z_a: int, z_b: int, x0: int, x1: int, y0: int, y1: int) -> tuple[int, int, bool] | None:
        hits: list[tuple[int, int]] = []
        saw_door = False
        for x in range(x0, x1 + 1):
            for y in range(y0, y1):
                a0 = lookup.block_id(x, y, z_a)
                a1 = lookup.block_id(x, y + 1, z_a)
                b0 = lookup.block_id(x, y, z_b)
                b1 = lookup.block_id(x, y + 1, z_b)
                if _is_passable(a0) and _is_passable(a1) and _is_passable(b0) and _is_passable(b1):
                    hits.append((x, y))
                    if a0 in door_ids or b0 in door_ids or a1 in door_ids or b1 in door_ids:
                        saw_door = True
                    break
        if len(hits) < 2:
            return None
        mid = hits[len(hits) // 2]
        return (int(mid[0]), int(mid[1]), saw_door)

    connections: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for i in range(len(rooms)):
        a = rooms[i]
        for j in range(i + 1, len(rooms)):
            b = rooms[j]
            pair = (a["id"], b["id"])
            if pair in seen_pairs:
                continue

            z_overlap0 = max(a["min_z"], b["min_z"])
            z_overlap1 = min(a["max_z"], b["max_z"])
            x_overlap0 = max(a["min_x"], b["min_x"])
            x_overlap1 = min(a["max_x"], b["max_x"])
            y_overlap0 = max(int(a["min_y"]) + y_shift, int(b["min_y"]) + y_shift)
            y_overlap1 = min(int(a["max_y"]) + y_shift, int(b["max_y"]) + y_shift)
            y0 = max(y_min_global, y_overlap0 - 1)
            y1 = min(y_max_global, y_overlap1 + 1)
            if y1 - y0 < 2:
                continue

            # Candidate shared face on X.
            if z_overlap1 - z_overlap0 >= 1:
                x_gap_1 = abs(a["max_x"] - b["min_x"])
                x_gap_2 = abs(b["max_x"] - a["min_x"])
                if x_gap_1 <= 1 or x_gap_2 <= 1:
                    if a["max_x"] <= b["min_x"]:
                        x_a = a["max_x"]
                        x_b = b["min_x"]
                    else:
                        x_a = b["max_x"]
                        x_b = a["min_x"]
                    scan = _scan_face_x(x_a, x_b, z_overlap0, z_overlap1, y0, y1)
                    if scan is not None:
                        z_mid, y_mid, has_door = scan
                        connections.append(
                            {
                                "a": int(a["id"]),
                                "b": int(b["id"]),
                                "axis": "x",
                                "x": int((x_a + x_b) // 2),
                                "y": int(y_mid),
                                "z": int(z_mid),
                                "kind": "door" if has_door else "open",
                            }
                        )
                        seen_pairs.add(pair)
                        continue

            # Candidate shared face on Z.
            if x_overlap1 - x_overlap0 >= 1:
                z_gap_1 = abs(a["max_z"] - b["min_z"])
                z_gap_2 = abs(b["max_z"] - a["min_z"])
                if z_gap_1 <= 1 or z_gap_2 <= 1:
                    if a["max_z"] <= b["min_z"]:
                        z_a = a["max_z"]
                        z_b = b["min_z"]
                    else:
                        z_a = b["max_z"]
                        z_b = a["min_z"]
                    scan = _scan_face_z(z_a, z_b, x_overlap0, x_overlap1, y0, y1)
                    if scan is not None:
                        x_mid, y_mid, has_door = scan
                        connections.append(
                            {
                                "a": int(a["id"]),
                                "b": int(b["id"]),
                                "axis": "z",
                                "x": int(x_mid),
                                "y": int(y_mid),
                                "z": int((z_a + z_b) // 2),
                                "kind": "door" if has_door else "open",
                            }
                        )
                        seen_pairs.add(pair)

    connections.sort(key=lambda c: (int(c.get("a", -1)), int(c.get("b", -1))))
    return connections


def _scan_world_chests(
    world_dir: Path,
    payload: dict[str, Any],
    bounds_2d: tuple[int, int, int, int],
) -> list[dict[str, Any]]:
    if anvil is None:
        return []
    region_dir = world_dir / "region"
    if not region_dir.exists():
        return []

    rooms = _payload_rooms(payload)
    y_shift = _estimate_room_y_shift(payload, rooms)
    x0, x1, z0, z1 = bounds_2d
    y0 = 0
    y1 = 127

    cx0 = x0 // 16
    cx1 = x1 // 16
    cz0 = z0 // 16
    cz1 = z1 // 16
    region_cache: dict[tuple[int, int], Any] = {}
    seen: set[tuple[int, int, int]] = set()
    out: list[dict[str, Any]] = []

    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            rx = cx // 32
            rz = cz // 32
            rkey = (rx, rz)
            region = region_cache.get(rkey)
            if region is None:
                rpath = region_dir / f"r.{rx}.{rz}.mca"
                if not rpath.exists():
                    region_cache[rkey] = False
                    continue
                try:
                    region = anvil.Region.from_file(str(rpath))
                except Exception:
                    region_cache[rkey] = False
                    continue
                region_cache[rkey] = region
            if region is False:
                continue
            local_cx = cx - (rx * 32)
            local_cz = cz - (rz * 32)
            try:
                chunk = region.get_chunk(local_cx, local_cz)
            except Exception:
                continue
            for lx in range(16):
                gx = cx * 16 + lx
                if gx < x0 or gx > x1:
                    continue
                for lz in range(16):
                    gz = cz * 16 + lz
                    if gz < z0 or gz > z1:
                        continue
                    for y in range(y0, y1 + 1):
                        try:
                            block = chunk.get_block(lx, y, lz)
                        except Exception:
                            continue
                        bid = str(getattr(block, "id", None) or getattr(block, "name", None) or block)
                        if bid not in ("chest", "minecraft:chest", "trapped_chest", "minecraft:trapped_chest"):
                            continue
                        key = (gx, y, gz)
                        if key in seen:
                            continue
                        seen.add(key)
                        chest_type = "trapped_chest" if "trapped" in bid else "chest"
                        room_id, room_type = _assign_room_for_block(gx, y, gz, rooms, y_shift=y_shift)
                        out.append(
                            {
                                "x": int(gx),
                                "y": int(y),
                                "z": int(gz),
                                "type": chest_type,
                                "room_id": int(room_id),
                                "room_type": room_type,
                            }
                        )

    out.sort(key=lambda c: (int(c.get("room_id", -1)), int(c.get("x", 0)), int(c.get("z", 0)), int(c.get("y", 0))))
    return out


def _door_y_spans_in_chunk(chunk: Any, y0: int, y1: int) -> list[tuple[int, int]]:
    sections = []
    try:
        sections = list(chunk.data.get("Sections", []))
    except Exception:
        return []
    out: list[tuple[int, int]] = []
    for sec in sections:
        try:
            sec_y = _nbt_int(sec.get("Y", 0), 0)
        except Exception:
            continue
        sec_min = sec_y * 16
        sec_max = sec_min + 15
        if sec_max < y0 or sec_min > y1:
            continue
        palette = sec.get("Palette")
        if not palette:
            continue
        has_door = False
        for entry in palette:
            try:
                name = _nbt_str(entry.get("Name", ""), "").lower()
            except Exception:
                continue
            if "oak_door" in name or "iron_door" in name:
                has_door = True
                break
        if not has_door:
            continue
        out.append((max(y0, sec_min), min(y1, sec_max)))
    return out


def _scan_world_features(
    world_dir: Path,
    payload: dict[str, Any],
    door_bounds_2d: tuple[int, int, int, int],
    bounds_3d: tuple[int, int, int, int, int, int] | None,
    *,
    spawner_extra_chunks: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if anvil is None:
        return ([], [], [])
    region_dir = world_dir / "region"
    if not region_dir.exists():
        return ([], [], [])

    rooms = _payload_rooms(payload)
    y_shift = _estimate_room_y_shift(payload, rooms)

    x0, x1, z0, z1 = door_bounds_2d
    if rooms:
        y_low = min(int(r.get("min_y", 0)) for r in rooms) + y_shift - 8
        y_high = max(int(r.get("max_y", 0)) for r in rooms) + y_shift + 8
        door_y0 = max(0, min(127, int(y_low)))
        door_y1 = max(0, min(127, int(y_high)))
        if door_y1 < door_y0:
            door_y0, door_y1 = 0, 127
    else:
        door_y0, door_y1 = 0, 127

    sp_scan_enabled = bounds_3d is not None
    sp_x0 = sp_x1 = sp_z0 = sp_z1 = 0
    if bounds_3d is not None:
        min_x, max_x, _min_y, _max_y, min_z, max_z = bounds_3d
        margin = max(0, int(spawner_extra_chunks)) * 16
        sp_x0 = int(min_x) - margin
        sp_x1 = int(max_x) + margin
        sp_z0 = int(min_z) - margin
        sp_z1 = int(max_z) + margin

    scan_x0 = min(x0, sp_x0) if sp_scan_enabled else x0
    scan_x1 = max(x1, sp_x1) if sp_scan_enabled else x1
    scan_z0 = min(z0, sp_z0) if sp_scan_enabled else z0
    scan_z1 = max(z1, sp_z1) if sp_scan_enabled else z1

    cx0 = scan_x0 // 16
    cx1 = scan_x1 // 16
    cz0 = scan_z0 // 16
    cz1 = scan_z1 // 16

    region_cache: dict[tuple[int, int], Any] = {}
    doors_seen: dict[tuple[int, int, str], tuple[int, str]] = {}
    chest_seen: set[tuple[int, int, int]] = set()
    spawner_seen: set[tuple[int, int, int, str]] = set()
    chests: list[dict[str, Any]] = []
    spawners: list[dict[str, Any]] = []

    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            rx = cx // 32
            rz = cz // 32
            rkey = (rx, rz)
            region = region_cache.get(rkey)
            if region is None:
                rpath = region_dir / f"r.{rx}.{rz}.mca"
                if not rpath.exists():
                    region_cache[rkey] = False
                    continue
                try:
                    region = anvil.Region.from_file(str(rpath))
                except Exception:
                    region_cache[rkey] = False
                    continue
                region_cache[rkey] = region
            if region is False:
                continue
            local_cx = cx - (rx * 32)
            local_cz = cz - (rz * 32)
            try:
                chunk = region.get_chunk(local_cx, local_cz)
            except Exception:
                continue

            # Tile-entity based extraction (cheap): chests and spawners.
            tile_entities: list[Any] = []
            try:
                tile_entities = list(chunk.tile_entities)
            except Exception:
                try:
                    tile_entities = list(chunk.data.get("TileEntities", []))
                except Exception:
                    tile_entities = []
            for te in tile_entities:
                te_id = _nbt_str(te.get("id", ""), "").lower()
                gx = _nbt_int(te.get("x", 0), 0)
                gy = _nbt_int(te.get("y", 0), 0)
                gz = _nbt_int(te.get("z", 0), 0)
                if te_id in ("chest", "minecraft:chest", "trapped_chest", "minecraft:trapped_chest"):
                    if gx < x0 or gx > x1 or gz < z0 or gz > z1:
                        pass
                    else:
                        key = (gx, gy, gz)
                        if key not in chest_seen:
                            chest_seen.add(key)
                            chest_type = "trapped_chest" if "trapped" in te_id else "chest"
                            room_id, room_type = _assign_room_for_block(gx, gy, gz, rooms, y_shift=y_shift)
                            chests.append(
                                {
                                    "x": int(gx),
                                    "y": int(gy),
                                    "z": int(gz),
                                    "type": chest_type,
                                    "room_id": int(room_id),
                                    "room_type": room_type,
                                }
                            )
                elif sp_scan_enabled and ("mob_spawner" in te_id or te_id.endswith(":spawner") or te_id == "spawner"):
                    if gx < sp_x0 or gx > sp_x1 or gz < sp_z0 or gz > sp_z1:
                        continue
                    mob_type = _extract_spawner_mob_type(te)
                    key = (gx, gy, gz, mob_type)
                    if key in spawner_seen:
                        continue
                    spawner_seen.add(key)
                    spawners.append({"x": int(gx), "y": int(gy), "z": int(gz), "mob_type": mob_type})

            # Block scan (expensive): only for doors, and only in sections whose palette includes door blocks.
            chunk_x0 = cx * 16
            chunk_z0 = cz * 16
            lx0 = max(0, x0 - chunk_x0)
            lx1 = min(15, x1 - chunk_x0)
            lz0 = max(0, z0 - chunk_z0)
            lz1 = min(15, z1 - chunk_z0)
            if lx0 > lx1 or lz0 > lz1:
                continue
            y_spans = _door_y_spans_in_chunk(chunk, door_y0, door_y1)
            if not y_spans:
                continue
            for lx in range(lx0, lx1 + 1):
                gx = chunk_x0 + lx
                for lz in range(lz0, lz1 + 1):
                    gz = chunk_z0 + lz
                    for ys0, ys1 in y_spans:
                        for y in range(ys0, ys1 + 1):
                            try:
                                block = chunk.get_block(lx, y, lz)
                            except Exception:
                                continue
                            bid = _nbt_str(getattr(block, "id", None) or getattr(block, "name", None) or block, "").lower()
                            if "oak_door" not in bid and "iron_door" not in bid:
                                continue
                            props = getattr(block, "properties", {}) or {}
                            half = str(props.get("half", "")).lower()
                            if half and half != "lower":
                                continue
                            facing = str(props.get("facing", "unknown")).lower()
                            door_type = "iron" if "iron_door" in bid else "oak"
                            key = (gx, gz, door_type)
                            prev = doors_seen.get(key)
                            if prev is None or y < int(prev[0]):
                                doors_seen[key] = (int(y), facing)

    doors: list[dict[str, Any]] = []
    for (x, z, door_type), (y, facing) in sorted(
        doors_seen.items(),
        key=lambda item: (int(item[1][0]), int(item[0][0]), int(item[0][1])),
    ):
        doors.append({"x": int(x), "y": int(y), "z": int(z), "type": door_type, "facing": facing})
    chests.sort(key=lambda c: (int(c.get("room_id", -1)), int(c.get("x", 0)), int(c.get("z", 0)), int(c.get("y", 0))))
    spawners.sort(key=lambda s: (int(s.get("x", 0)), int(s.get("z", 0)), int(s.get("y", 0))))
    return (doors, chests, spawners)


def _extract_spawner_mob_type(tile_entity: Any) -> str:
    def _nbt_str(value: Any) -> str:
        return str(getattr(value, "value", value))

    def _extract_from_compound(compound: Any) -> str | None:
        if not hasattr(compound, "get"):
            return None
        entity_id = compound.get("id")
        if entity_id is not None:
            return _nbt_str(entity_id)
        entity = compound.get("entity")
        if hasattr(entity, "get"):
            sub_id = entity.get("id")
            if sub_id is not None:
                return _nbt_str(sub_id)
        data = compound.get("data")
        if hasattr(data, "get"):
            sub_id = data.get("id")
            if sub_id is not None:
                return _nbt_str(sub_id)
            sub_entity = data.get("entity")
            if hasattr(sub_entity, "get"):
                sub_sub_id = sub_entity.get("id")
                if sub_sub_id is not None:
                    return _nbt_str(sub_sub_id)
        return None

    spawn_data = tile_entity.get("SpawnData")
    mob = _extract_from_compound(spawn_data)
    if mob is None:
        spawn_potentials = tile_entity.get("SpawnPotentials")
        if isinstance(spawn_potentials, list) and spawn_potentials:
            for entry in spawn_potentials:
                mob = _extract_from_compound(entry)
                if mob is not None:
                    break
    if mob is None:
        return "unknown"
    mob = _nbt_str(mob).strip().lower()
    if mob.startswith("minecraft:"):
        mob = mob.split(":", 1)[1]
    return mob or "unknown"


def _scan_world_spawners(
    world_dir: Path,
    bounds_3d: tuple[int, int, int, int, int, int],
    *,
    extra_chunks: int = 2,
) -> list[dict[str, Any]]:
    if anvil is None:
        return []
    region_dir = world_dir / "region"
    if not region_dir.exists():
        return []

    min_x, max_x, _min_y, _max_y, min_z, max_z = bounds_3d
    margin = max(0, int(extra_chunks)) * 16
    x0 = int(min_x) - margin
    x1 = int(max_x) + margin
    z0 = int(min_z) - margin
    z1 = int(max_z) + margin

    cx0 = x0 // 16
    cx1 = x1 // 16
    cz0 = z0 // 16
    cz1 = z1 // 16
    region_cache: dict[tuple[int, int], Any] = {}
    seen: set[tuple[int, int, int, str]] = set()
    out: list[dict[str, Any]] = []

    def _nbt_int(value: Any) -> int:
        return int(getattr(value, "value", value))

    def _nbt_str(value: Any) -> str:
        return str(getattr(value, "value", value))

    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            rx = cx // 32
            rz = cz // 32
            rkey = (rx, rz)
            region = region_cache.get(rkey)
            if region is None:
                rpath = region_dir / f"r.{rx}.{rz}.mca"
                if not rpath.exists():
                    region_cache[rkey] = False
                    continue
                try:
                    region = anvil.Region.from_file(str(rpath))
                except Exception:
                    region_cache[rkey] = False
                    continue
                region_cache[rkey] = region
            if region is False:
                continue
            local_cx = cx - (rx * 32)
            local_cz = cz - (rz * 32)
            try:
                chunk = region.get_chunk(local_cx, local_cz)
            except Exception:
                continue

            tile_entities: list[Any] = []
            try:
                tile_entities = list(chunk.tile_entities)
            except Exception:
                try:
                    tile_entities = list(chunk.data.get("TileEntities", []))
                except Exception:
                    tile_entities = []
            for te in tile_entities:
                try:
                    te_id = _nbt_str(te.get("id", "")).lower()
                except Exception:
                    te_id = ""
                if "mob_spawner" not in te_id and not te_id.endswith(":spawner") and te_id != "spawner":
                    continue
                try:
                    gx = _nbt_int(te.get("x"))
                    gy = _nbt_int(te.get("y"))
                    gz = _nbt_int(te.get("z"))
                except Exception:
                    continue
                if gx < x0 or gx > x1 or gz < z0 or gz > z1:
                    continue
                mob_type = _extract_spawner_mob_type(te)
                key = (gx, gy, gz, mob_type)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "x": int(gx),
                        "y": int(gy),
                        "z": int(gz),
                        "mob_type": mob_type,
                    }
                )

    out.sort(key=lambda s: (int(s.get("x", 0)), int(s.get("z", 0)), int(s.get("y", 0))))
    return out


def _filter_nav_window_samples(
    samples: list[dict[str, Any]],
    *,
    eye_gt: int,
    end_gt: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sample in samples:
        gt = int(sample.get("gt", 0) or 0)
        if eye_gt > 0 and gt < eye_gt:
            continue
        if end_gt > 0 and gt > end_gt:
            continue
        out.append(sample)
    return out


def _trim_samples_on_large_jump(
    samples: list[dict[str, Any]],
    *,
    max_step_blocks: float = 80.0,
) -> list[dict[str, Any]]:
    if not samples:
        return []
    trimmed: list[dict[str, Any]] = [samples[0]]
    prev = samples[0]
    for sample in samples[1:]:
        try:
            px = float(int(prev.get("x", 0) or 0)) / 1000.0
            pz = float(int(prev.get("z", 0) or 0)) / 1000.0
            sx = float(int(sample.get("x", 0) or 0)) / 1000.0
            sz = float(int(sample.get("z", 0) or 0)) / 1000.0
        except Exception:
            break
        dx = sx - px
        dz = sz - pz
        step = (dx * dx + dz * dz) ** 0.5
        if step > max_step_blocks:
            break
        trimmed.append(sample)
        prev = sample
    return trimmed


def _diagnose_storage(path: Path) -> tuple[list[str], list[str]]:
    top_keys: list[str] = []
    contents_keys: list[str] = []
    try:
        with gzip.open(path, "rb") as f:
            nbt_file = nbtlib.File.parse(f)
        root = nbt_file.root if hasattr(nbt_file, "root") else nbt_file
        if isinstance(root, dict):
            top_keys = [str(k) for k in root.keys()]
            data_node = root.get("data")
            if isinstance(data_node, dict):
                contents = data_node.get("contents")
                if isinstance(contents, dict):
                    contents_keys = [str(k) for k in contents.keys()]
    except Exception:
        pass
    return top_keys, contents_keys


def _run(cmd: list[str], *, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


class _TimingLog:
    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.start = time.perf_counter()
        self.last = self.start

    def mark(self, label: str) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        delta = now - self.last
        total = now - self.start
        self.last = now
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[timing {ts}] {label}: +{delta*1000.0:.1f}ms (total {total:.3f}s)")


def _parse_f3c_tp_line(line: str | None) -> dict[str, Any] | None:
    raw = str(line or "").strip()
    if not raw:
        return None
    match = _F3C_TP_RE.match(raw)
    if not match:
        return None
    try:
        out = {
            "source": "f3c",
            "raw": raw,
            "dim": str(match.group("dim")).lower(),
            "x": float(match.group("x")),
            "y": float(match.group("y")),
            "z": float(match.group("z")),
            "yaw": float(match.group("yaw")),
            "pitch": float(match.group("pitch")),
        }
    except Exception:
        return None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build stronghold room map/path from a world folder using tracker samples + seed."
    )
    parser.add_argument("world", help="Path to world folder (contains level.dat and data/command_storage_*.dat).")
    parser.add_argument(
        "--out",
        help="Output JSON path. Default: <repo>/data/stronghold_maps/<world>.json",
    )
    parser.add_argument(
        "--samples-csv",
        help="Optional path for intermediate samples CSV. Default: <repo>/data/stronghold_maps/<world>_samples.csv",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuilding Java stronghold cracker classes before running.",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Write rooms-entered/starter-time stats back into the latest MPK attempt for this world.",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Skip writing topdown SVG render (default writes next to JSON output in repo data folder).",
    )
    parser.add_argument(
        "--aim-line",
        action="append",
        default=[],
        help=(
            "Optional F3+C clipboard line (/execute ... tp @s x y z yaw pitch) to draw orientation rays on the map. "
            "Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Print timestamped timing diagnostics for major analysis phases.",
    )
    args = parser.parse_args()
    timing = _TimingLog(bool(args.timing))
    timing.mark("args parsed")

    world_dir = Path(args.world)
    if not world_dir.exists() or not world_dir.is_dir():
        print(f"World directory not found: {world_dir}")
        return 2
    data_dir = world_dir / "data"
    storage_path = _find_storage_file(data_dir)
    if storage_path is None:
        print(f"No command_storage file found in: {data_dir}")
        return 2

    metrics = run_metrics_from_storage(storage_path)
    timing.mark("storage parsed")
    if not metrics:
        top_keys, contents_keys = _diagnose_storage(storage_path)
        print(f"No zdash tracker data found in: {storage_path}")
        if top_keys:
            print(f"NBT top-level keys: {', '.join(top_keys)}")
        if contents_keys:
            print(f"Storage contents keys: {', '.join(contents_keys)}")
        print("This world was likely created without the injected zdash_tracker datapack (or before it was updated).")
        print("Run a fresh world with injection enabled, throw Eye Spy, then enter the End, and retry.")
        return 2
    samples = metrics.get("stronghold_samples", [])
    if not isinstance(samples, list) or not samples:
        eye_logged = bool(metrics.get("stronghold_eye_spy_logged", False))
        eye_gt = int(metrics.get("stronghold_eye_spy_gt", 0) or 0)
        end_logged = bool(metrics.get("stronghold_end_enter_logged", False))
        end_gt = int(metrics.get("stronghold_end_enter_gt", 0) or 0)
        sh_active = bool(metrics.get("stronghold_active", False))
        print("No stronghold samples found in storage.")
        print(
            "Stronghold tracker state: "
            + f"eye_spy.logged={eye_logged} (gt={eye_gt}), "
            + f"end_enter.logged={end_logged} (gt={end_gt}), "
            + f"active={sh_active}"
        )
        if not eye_logged:
            print("No Eye Spy event was captured in this world (or tracker was not active when Eye Spy happened).")
        elif eye_logged and not end_logged:
            print("Eye Spy was captured, but End entry was not captured yet in this world.")
        else:
            print("Tracker metadata exists, but sample list is empty (unexpected).")
        return 2
    eye_gt = int(metrics.get("stronghold_eye_spy_gt", 0) or 0)
    end_gt = int(metrics.get("stronghold_end_enter_gt", 0) or 0)
    if eye_gt > 0 or end_gt > 0:
        samples = _filter_nav_window_samples(samples, eye_gt=eye_gt, end_gt=end_gt)
    samples = _trim_samples_on_large_jump(samples, max_step_blocks=80.0)
    timing.mark("samples filtered")
    if not samples:
        print(
            "No stronghold samples remain after eye/end window filter: "
            + f"eye_gt={eye_gt}, end_gt={end_gt}"
        )
        return 2

    seed = world_seed_from_level_dat(world_dir)
    timing.mark("seed loaded")
    if seed is None:
        print(f"Could not read world seed from: {world_dir / 'level.dat'}")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    cracker_dir = repo_root / "tools" / "stronghold_cracker"
    classes_dir = cracker_dir / "build" / "classes"
    build_script = cracker_dir / "build.bat"

    default_out_path, default_samples_csv_path = _default_outputs_for_world(world_dir)
    out_path = Path(args.out) if args.out else default_out_path
    samples_csv_path = Path(args.samples_csv) if args.samples_csv else default_samples_csv_path
    _write_samples_csv(samples_csv_path, samples)
    timing.mark("samples csv written")

    if args.rebuild or not classes_dir.exists():
        if not build_script.exists():
            print(f"Missing build script: {build_script}")
            return 2
        _run(["cmd", "/c", str(build_script)], cwd=cracker_dir)
        timing.mark("cracker rebuilt")

    classpath = f"{classes_dir};{cracker_dir / 'lib'}/*"
    cmd = [
        "java",
        "-cp",
        classpath,
        "zdash.stronghold.StrongholdCrackerMain",
        "--seed",
        str(int(seed)),
        "--samples",
        str(samples_csv_path),
        "--out",
        str(out_path),
    ]
    _run(cmd, cwd=repo_root)
    timing.mark("cracker executed")

    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        print(f"Wrote map JSON: {out_path}")
        return 0
    timing.mark("map json loaded")

    bounds = _map_bounds_from_payload(payload)
    accepted_manual_aims: list[dict[str, Any]] = []
    seen_raw: set[str] = set()
    for raw_line in list(args.aim_line or []):
        manual_aim = _parse_f3c_tp_line(str(raw_line or ""))
        if manual_aim is None:
            continue
        raw_key = str(manual_aim.get("raw", ""))
        if not raw_key or raw_key in seen_raw:
            continue
        seen_raw.add(raw_key)
        accepted = False
        if bounds is not None:
            min_x, max_x, _min_y, _max_y, min_z, max_z = bounds
            margin = 24.0
            dim = str(manual_aim.get("dim", "")).lower()
            x = float(manual_aim.get("x", 0.0))
            z = float(manual_aim.get("z", 0.0))
            accepted = (
                dim in ("minecraft:overworld", "overworld")
                and x >= (float(min_x) - margin)
                and x <= (float(max_x) + margin)
                and z >= (float(min_z) - margin)
                and z <= (float(max_z) + margin)
            )
        if accepted:
            accepted_manual_aims.append(manual_aim)
    payload["manual_aims"] = accepted_manual_aims
    if accepted_manual_aims:
        payload["manual_aim"] = accepted_manual_aims[-1]
        print(f"  Manual aims: accepted {len(accepted_manual_aims)}")
    elif args.aim_line:
        print("  Manual aims: 0 accepted (outside stronghold bounds or non-overworld command).")
    timing.mark("manual aims processed")
    door_bounds = _door_scan_bounds_from_payload(payload)
    if door_bounds is not None:
        doors, chests, spawners = _scan_world_features(
            world_dir,
            payload,
            door_bounds,
            bounds,
            spawner_extra_chunks=2,
        )
        payload["doors"] = doors
        payload["chests"] = chests
        payload["spawners"] = spawners
    else:
        payload["doors"] = []
        payload["chests"] = []
        payload["spawners"] = []
    timing.mark("doors chests spawners scanned (combined)")

    room_masks: list[dict[str, Any]] = []
    windows: dict[int, tuple[int, int]] = {}
    if anvil is not None:
        room_masks, windows = _scan_room_masks(world_dir, payload)
    payload["room_masks"] = room_masks
    timing.mark("room masks scanned")

    connection_polygons: list[dict[str, Any]] = []
    if anvil is not None and windows:
        connection_polygons = _scan_connection_polygons(world_dir, payload, windows)
    payload["connection_polygons"] = connection_polygons
    timing.mark("connection polygons scanned")

    if connection_polygons:
        payload["connections"] = _connections_from_polygons(connection_polygons)
    else:
        payload["connections"] = _scan_world_connections(world_dir, payload)
    timing.mark("connections finalized")

    payload["optimal_nav"] = _compute_optimal_nav(payload)
    timing.mark("optimal nav computed")

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    timing.mark("output json written")

    starter = payload.get("starter", {})
    sample_stats = payload.get("sample_stats", {})
    print("Stronghold analysis:")
    print(f"  World: {world_dir}")
    print(f"  Seed: {seed}")
    print(
        "  Samples: "
        f"{sample_stats.get('used_for_mapping', 0)}/{sample_stats.get('total', 0)}"
    )
    print(
        "  Rooms entered: "
        f"{sample_stats.get('mapped_rooms', 0)}"
    )
    print(
        "  Starter dwell: "
        f"{starter.get('ticks', 0)} ticks ({starter.get('seconds', 0.0)}s)"
    )
    print(f"  Doors: {len(payload.get('doors', []))}")
    print(f"  Chests: {len(payload.get('chests', []))}")
    print(f"  Spawners: {len(payload.get('spawners', []))}")
    print(f"  Room masks: {len(payload.get('room_masks', []))}")
    print(f"  Connection polygons: {len(payload.get('connection_polygons', []))}")
    print(f"  Connections: {len(payload.get('connections', []))}")
    optimal_nav = payload.get("optimal_nav", {})
    if bool(optimal_nav.get("reachable", False)):
        print(
            "  Optimal nav: "
            + f"{int(optimal_nav.get('min_rooms', -1))} rooms "
            + f"({int(optimal_nav.get('min_edges', -1))} transitions) "
            + f"starter#{int(optimal_nav.get('starter_room_id', -1))} -> portal#{int(optimal_nav.get('target_portal_room_id', -1))}"
        )
    else:
        print("  Optimal nav: unreachable/unknown")
    print(f"  Output: {out_path}")
    if not bool(args.no_render):
        try:
            svg_path = render_map_svg(out_path)
            print(f"  Topdown SVG: {svg_path}")
        except Exception as exc:
            print(f"  Topdown SVG error: {exc}")
    timing.mark("render stage done")

    if args.update_db:
        rooms_entered = int(sample_stats.get("mapped_rooms", 0) or 0)
        starter_ticks = int(starter.get("ticks", 0) or 0)
        starter_seconds = float(starter.get("seconds", 0.0) or 0.0)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE attempts
                SET
                    stronghold_rooms_entered = ?,
                    stronghold_starter_ticks = ?,
                    stronghold_starter_seconds = ?
                WHERE id = (
                    SELECT id
                    FROM attempts
                    WHERE COALESCE(attempt_source, 'practice') = 'mpk'
                      AND world_name = ?
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (rooms_entered, starter_ticks, starter_seconds, world_dir.name),
            )
            conn.commit()
        print(f"  DB update: wrote rooms/starter stats into latest MPK attempt for world '{world_dir.name}'.")
    timing.mark("db update done")
    timing.mark("complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
