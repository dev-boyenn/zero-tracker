from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any

try:
    import anvil  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Missing dependency anvil-parser: {exc}")


PASSABLE = {
    "air",
    "cave_air",
    "void_air",
    "oak_door",
    "iron_door",
    "iron_bars",
    "torch",
    "wall_torch",
    "chest",
    "trapped_chest",
}


def _world_id_from_map_path(map_path: Path) -> str:
    stem = map_path.stem
    return stem


def _resolve_world_dir(worlds_root: Path, world_id: str) -> Path:
    direct = worlds_root / world_id
    if direct.exists():
        return direct
    prefixed = worlds_root / f"Random Speedrun #{world_id}"
    if prefixed.exists():
        return prefixed
    return direct


class _Lookup:
    def __init__(self, world_dir: Path) -> None:
        self.region_dir = world_dir / "region"
        self.region_cache: dict[tuple[int, int], Any] = {}
        self.chunk_cache: dict[tuple[int, int], Any] = {}

    def _region(self, rx: int, rz: int) -> Any | None:
        key = (rx, rz)
        cached = self.region_cache.get(key)
        if cached is not None:
            return None if cached is False else cached
        path = self.region_dir / f"r.{rx}.{rz}.mca"
        if not path.exists():
            self.region_cache[key] = False
            return None
        try:
            region = anvil.Region.from_file(str(path))
        except Exception:
            self.region_cache[key] = False
            return None
        self.region_cache[key] = region
        return region

    def _chunk(self, cx: int, cz: int) -> Any | None:
        key = (cx, cz)
        cached = self.chunk_cache.get(key)
        if cached is not None:
            return None if cached is False else cached
        rx = cx // 32
        rz = cz // 32
        region = self._region(rx, rz)
        if region is None:
            self.chunk_cache[key] = False
            return None
        local_cx = cx - (rx * 32)
        local_cz = cz - (rz * 32)
        try:
            chunk = region.get_chunk(local_cx, local_cz)
        except Exception:
            self.chunk_cache[key] = False
            return None
        self.chunk_cache[key] = chunk
        return chunk

    def block_id(self, x: int, y: int, z: int) -> str:
        if y < 0 or y > 255:
            return ""
        cx = x // 16
        cz = z // 16
        chunk = self._chunk(cx, cz)
        if chunk is None:
            return ""
        lx = x - (cx * 16)
        lz = z - (cz * 16)
        try:
            block = chunk.get_block(lx, y, lz)
        except Exception:
            return ""
        return str(getattr(block, "id", None) or block).lower()


def _is_passable(block_id: str) -> bool:
    return block_id in PASSABLE


def _estimate_global_shift(payload: dict[str, Any]) -> int:
    rooms_by_id: dict[int, dict[str, Any]] = {}
    for room in payload.get("pieces", []):
        if not isinstance(room, dict):
            continue
        rid = int(room.get("id", -1) or -1)
        if rid < 0:
            continue
        rooms_by_id[rid] = room
    deltas: list[float] = []
    for point in payload.get("path", []):
        if not isinstance(point, dict):
            continue
        if int(point.get("dim", 0) or 0) != 0:
            continue
        rid = int(point.get("room_id", -1) or -1)
        room = rooms_by_id.get(rid)
        if room is None:
            continue
        try:
            py = float(point.get("y"))
            min_y = float(room.get("min_y"))
            max_y = float(room.get("max_y"))
        except Exception:
            continue
        center = (min_y + max_y) * 0.5
        deltas.append(py - center)
    if not deltas:
        return 0
    return int(round(median(deltas)))


def _room_point_ys(payload: dict[str, Any]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for point in payload.get("path", []):
        if not isinstance(point, dict):
            continue
        if int(point.get("dim", 0) or 0) != 0:
            continue
        rid = int(point.get("room_id", -1) or -1)
        if rid < 0:
            continue
        try:
            y = int(round(float(point.get("y"))))
        except Exception:
            continue
        out.setdefault(rid, []).append(y)
    return out


def _scan_room_cells(
    lookup: _Lookup,
    room: dict[str, Any],
    *,
    room_shift: int,
) -> set[tuple[int, int]]:
    min_x = int(room["min_x"])
    max_x = int(room["max_x"])
    min_y = int(room["min_y"]) + room_shift
    max_y = int(room["max_y"]) + room_shift
    min_z = int(room["min_z"])
    max_z = int(room["max_z"])
    y0 = max(1, min_y - 2)
    y1 = min(127, max_y + 2)

    cells: set[tuple[int, int]] = set()
    for z in range(min_z, max_z + 1):
        for x in range(min_x, max_x + 1):
            walkable = False
            for y in range(y0, y1):
                b0 = lookup.block_id(x, y, z)
                b1 = lookup.block_id(x, y + 1, z)
                below = lookup.block_id(x, y - 1, z)
                if _is_passable(b0) and _is_passable(b1) and not _is_passable(below):
                    walkable = True
                    break
            if walkable:
                cells.add((x, z))
    return cells


def _local_coord(
    x: int,
    z: int,
    room: dict[str, Any],
) -> tuple[int, int]:
    # Keep templates facing-specific for now (no extra normalization),
    # but coordinates are local to piece bbox.
    return (x - int(room["min_x"]), z - int(room["min_z"]))


def build_templates(map_path: Path, worlds_root: Path, out_path: Path) -> int:
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    world_id = _world_id_from_map_path(map_path)
    world_dir = _resolve_world_dir(worlds_root, world_id)
    if not world_dir.exists():
        raise FileNotFoundError(f"World folder not found for map id '{world_id}': {world_dir}")
    if not (world_dir / "region").exists():
        raise FileNotFoundError(f"Missing region folder: {world_dir / 'region'}")

    lookup = _Lookup(world_dir)
    global_shift = _estimate_global_shift(payload)
    y_samples = _room_point_ys(payload)

    templates: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    rooms_total = 0
    rooms_scanned = 0
    for room in payload.get("pieces", []):
        if not isinstance(room, dict):
            continue
        try:
            rid = int(room.get("id", -1) or -1)
            rtype = str(room.get("type", "Unknown"))
            facing = str(room.get("facing", "NONE")).upper()
            min_x = int(room.get("min_x"))
            max_x = int(room.get("max_x"))
            min_z = int(room.get("min_z"))
            max_z = int(room.get("max_z"))
        except Exception:
            continue
        if rid < 0:
            continue
        rooms_total += 1
        width = (max_x - min_x) + 1
        depth = (max_z - min_z) + 1

        ys = y_samples.get(rid, [])
        if ys:
            room_shift = int(round(median(ys) - ((int(room["min_y"]) + int(room["max_y"])) * 0.5)))
        else:
            room_shift = global_shift
        cells = _scan_room_cells(lookup, room, room_shift=room_shift)
        if not cells:
            continue
        rooms_scanned += 1

        key = (rtype, facing, width, depth)
        entry = templates.get(key)
        if entry is None:
            entry = {
                "type": rtype,
                "facing": facing,
                "width": width,
                "depth": depth,
                "instances": 0,
                "cell_hits": {},
            }
            templates[key] = entry
        entry["instances"] = int(entry["instances"]) + 1

        cell_hits: dict[str, int] = entry["cell_hits"]
        for x, z in cells:
            lx, lz = _local_coord(x, z, room)
            ckey = f"{lx},{lz}"
            cell_hits[ckey] = int(cell_hits.get(ckey, 0)) + 1

    out_templates: list[dict[str, Any]] = []
    for key in sorted(templates.keys()):
        entry = templates[key]
        instances = int(entry["instances"])
        cell_hits = entry["cell_hits"]
        cells: list[dict[str, Any]] = []
        runs_by_z: dict[int, list[int]] = {}
        for ckey, hit in cell_hits.items():
            lx_s, lz_s = ckey.split(",")
            lx = int(lx_s)
            lz = int(lz_s)
            ratio = float(hit) / float(instances) if instances > 0 else 0.0
            cells.append({"x": lx, "z": lz, "hits": int(hit), "ratio": ratio})
            if ratio >= 0.5:
                runs_by_z.setdefault(lz, []).append(lx)
        cells.sort(key=lambda c: (int(c["z"]), int(c["x"])))

        runs: list[dict[str, int]] = []
        for z, xs in sorted(runs_by_z.items()):
            xs_sorted = sorted(set(xs))
            if not xs_sorted:
                continue
            start = xs_sorted[0]
            prev = xs_sorted[0]
            for x in xs_sorted[1:]:
                if x == prev + 1:
                    prev = x
                    continue
                runs.append({"z": int(z), "x0": int(start), "x1": int(prev)})
                start = x
                prev = x
            runs.append({"z": int(z), "x0": int(start), "x1": int(prev)})

        out_templates.append(
            {
                "type": entry["type"],
                "facing": entry["facing"],
                "width": int(entry["width"]),
                "depth": int(entry["depth"]),
                "instances": instances,
                "cells": cells,
                "runs_ratio_ge_0_5": runs,
            }
        )

    out = {
        "map": str(map_path),
        "world": str(world_dir),
        "rooms_total": rooms_total,
        "rooms_scanned": rooms_scanned,
        "templates": out_templates,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote templates: {out_path}")
    print(f"Rooms scanned: {rooms_scanned}/{rooms_total}")
    print(f"Template keys: {len(out_templates)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build per-piece stronghold geometry templates from real world block data."
    )
    parser.add_argument("map_json", help="Path to stronghold map json (e.g. data/stronghold_maps/2346450.json)")
    parser.add_argument(
        "--worlds-root",
        default=r"C:\Users\Boyen\Desktop\MultiMC\instances\Ranked\.minecraft\saves",
        help="Root folder containing world saves.",
    )
    parser.add_argument(
        "--out",
        help="Output template json path. Default: data/stronghold_maps/<id>_templates.json",
    )
    args = parser.parse_args()

    map_path = Path(args.map_json)
    if not map_path.exists():
        print(f"Map json not found: {map_path}")
        return 2
    out_path = Path(args.out) if args.out else map_path.with_name(f"{map_path.stem}_templates.json")
    return build_templates(map_path, Path(args.worlds_root), out_path)


if __name__ == "__main__":
    raise SystemExit(main())

