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
    args = parser.parse_args()

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
    if not samples:
        print(
            "No stronghold samples remain after eye/end window filter: "
            + f"eye_gt={eye_gt}, end_gt={end_gt}"
        )
        return 2

    seed = world_seed_from_level_dat(world_dir)
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

    if args.rebuild or not classes_dir.exists():
        if not build_script.exists():
            print(f"Missing build script: {build_script}")
            return 2
        _run(["cmd", "/c", str(build_script)], cwd=cracker_dir)

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

    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        print(f"Wrote map JSON: {out_path}")
        return 0
    door_bounds = _door_scan_bounds_from_payload(payload)
    if door_bounds is not None:
        doors = _scan_world_doors(world_dir, door_bounds)
        payload["doors"] = doors
        payload["chests"] = _scan_world_chests(world_dir, payload, door_bounds)
    payload["connections"] = _scan_world_connections(world_dir, payload)
    if door_bounds is not None:
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

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
    print(f"  Connections: {len(payload.get('connections', []))}")
    print(f"  Output: {out_path}")
    if not bool(args.no_render):
        try:
            svg_path = render_map_svg(out_path)
            print(f"  Topdown SVG: {svg_path}")
        except Exception as exc:
            print(f"  Topdown SVG error: {exc}")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
