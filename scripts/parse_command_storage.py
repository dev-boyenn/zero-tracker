

from __future__ import annotations

import argparse
import gzip
import math
from pathlib import Path
from typing import Any

import nbtlib
try:
    import anvil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    anvil = None


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    try:
        return int(value)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        pass
    return value


def _find_samples(node: Any, path: str = "") -> tuple[list[dict[str, Any]] | None, str | None]:
    plain = _to_plain(node)
    if isinstance(plain, dict):
        maybe = plain.get("samples")
        if isinstance(maybe, list):
            if all(isinstance(x, dict) and {"x", "y", "z"}.issubset(set(x.keys())) for x in maybe):
                return maybe, (path + ".samples" if path else "samples")
        for k, v in plain.items():
            child_path = f"{path}.{k}" if path else str(k)
            found, found_path = _find_samples(v, child_path)
            if found is not None:
                return found, found_path
    elif isinstance(plain, list):
        for i, v in enumerate(plain):
            child_path = f"{path}[{i}]"
            found, found_path = _find_samples(v, child_path)
            if found is not None:
                return found, found_path
    return None, None


def _find_tracker(node: Any, path: str = "") -> tuple[dict[str, Any] | None, str | None]:
    plain = _to_plain(node)
    if isinstance(plain, dict):
        maybe = plain.get("tracker")
        if isinstance(maybe, dict):
            return maybe, (path + ".tracker" if path else "tracker")
        for k, v in plain.items():
            child_path = f"{path}.{k}" if path else str(k)
            found, found_path = _find_tracker(v, child_path)
            if found is not None:
                return found, found_path
    elif isinstance(plain, list):
        for i, v in enumerate(plain):
            child_path = f"{path}[{i}]"
            found, found_path = _find_tracker(v, child_path)
            if found is not None:
                return found, found_path
    return None, None


def _classify_node(samples: list[dict[str, Any]], *, window_ticks: int) -> dict[str, int]:
    nodes = {
        "back_diag": (-30.0, 27.0),
        "front_diag": (29.0, -29.0),
        "back_straight": (-21.0, 0.0),
        "front_straight": (20.0, 0.0),
    }
    counts = {k: 0 for k in nodes}
    if not samples:
        return counts
    first_gt = int(samples[0].get("gt", 0))
    max_gt = first_gt + window_ticks
    for s in samples:
        gt = int(s.get("gt", 0))
        if gt > max_gt:
            break
        x = int(s.get("x", 0)) / 1000.0
        z = int(s.get("z", 0)) / 1000.0
        best = None
        best_d = None
        for name, (nx, nz) in nodes.items():
            d = math.hypot(x - nx, z - nz)
            if best_d is None or d < best_d:
                best_d = d
                best = name
        if best is not None:
            counts[best] += 1
    return counts


def dominant_node_from_storage(path: Path, window_ticks: int = 600) -> tuple[str | None, dict[str, int]]:
    if not path.exists():
        return None, {}
    with gzip.open(path, "rb") as f:
        nbt_file = nbtlib.File.parse(f)
    samples, _ = _find_samples(nbt_file)
    if not samples:
        return None, {}
    counts = _classify_node(samples, window_ticks=window_ticks)
    if not counts:
        return None, counts
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    return dominant, counts


def rotation_from_storage(path: Path, window_ticks: int = 600) -> str:
    if not path.exists():
        return "unknown"
    with gzip.open(path, "rb") as f:
        nbt_file = nbtlib.File.parse(f)
    samples, _ = _find_samples(nbt_file)
    if not samples:
        return "unknown"
    first_gt = int(samples[0].get("gt", 0))
    max_gt = first_gt + window_ticks
    last_angle = None
    net = 0.0
    for s in samples:
        gt = int(s.get("gt", 0))
        if gt > max_gt:
            break
        x = int(s.get("x", 0)) / 1000.0
        z = int(s.get("z", 0)) / 1000.0
        angle = math.atan2(z, x)
        if last_angle is not None:
            delta = angle - last_angle
            while delta <= -math.pi:
                delta += 2 * math.pi
            while delta > math.pi:
                delta -= 2 * math.pi
            net += delta
        last_angle = angle
    if abs(net) < 0.2:
        return "unknown"
    return "ccw" if net > 0 else "cw"


def _parse_explode_events(raw_events: Any) -> list[dict[str, int]]:
    if not isinstance(raw_events, list):
        return []
    parsed: list[dict[str, int]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        gt = int(item.get("gt", 0) or 0)
        explode_beds = int(item.get("explode_beds", 0) or 0)
        explode_anchors_raw = int(item.get("explode_anchors", 0) or 0)
        # Anchor drop detection can double count around the player; normalize
        # to explosion count similarly to bed two-block normalization.
        explode_anchors = (explode_anchors_raw + 1) // 2 if explode_anchors_raw > 0 else 0
        if explode_beds <= 0 and explode_anchors <= 0:
            continue
        parsed.append({"gt": gt, "explode_beds": explode_beds, "explode_anchors": explode_anchors})
    parsed.sort(key=lambda e: e["gt"])
    return parsed


def _parse_damage_events(raw_events: Any) -> list[dict[str, int]]:
    if not isinstance(raw_events, list):
        return []
    parsed: list[dict[str, int]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        gt = int(item.get("gt", 0) or 0)
        hp_diff_scaled = int(item.get("hp_diff_scaled", 0) or 0)
        if hp_diff_scaled <= 0:
            continue
        explode_anchors_raw = int(item.get("explode_anchors", 0) or 0)
        explode_anchors = (explode_anchors_raw + 1) // 2 if explode_anchors_raw > 0 else 0
        parsed.append(
            {
                "gt": gt,
                "hp_diff_scaled": hp_diff_scaled,
                "explode_beds": int(item.get("explode_beds", 0) or 0),
                "explode_anchors": explode_anchors,
                "near_bed_drop": int(item.get("near_bed_drop", 0) or 0),
                "near_anchor_drop": int(item.get("near_anchor_drop", 0) or 0),
                "bed_dmg_scaled": int(item.get("bed_dmg_scaled", 0) or 0),
                "anchor_dmg_scaled": int(item.get("anchor_dmg_scaled", 0) or 0),
                "other_dmg_scaled": int(item.get("other_dmg_scaled", 0) or 0),
            }
        )
    parsed.sort(key=lambda e: e["gt"])
    return parsed


def _attribute_damage_from_events(
    explode_events: list[dict[str, int]],
    damage_events: list[dict[str, int]],
    *,
    tight_bed_window_ticks: int = 24,
    tight_anchor_window_ticks: int = 48,
    bed_window_ticks: int = 220,
    anchor_window_ticks: int = 320,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    use_items: list[dict[str, Any]] = []
    for ev in explode_events:
        for _ in range(max(0, ev["explode_beds"])):
            use_items.append({"gt": ev["gt"], "source": "bed", "used": False})
        for _ in range(max(0, ev["explode_anchors"])):
            use_items.append({"gt": ev["gt"], "source": "anchor", "used": False})

    mapped: list[dict[str, Any]] = [
        {
            "gt": int(ev["gt"]),
            "hp_diff_scaled": int(ev["hp_diff_scaled"]),
            "explode_beds": int(ev.get("explode_beds", 0)),
            "explode_anchors": int(ev.get("explode_anchors", 0)),
            "near_bed_drop": int(ev.get("near_bed_drop", 0)),
            "near_anchor_drop": int(ev.get("near_anchor_drop", 0)),
            "source": "other",
            "matched_use_gt": None,
            "matched_dt": None,
        }
        for ev in damage_events
    ]

    def _match_damage_events(*, bed_window: int, anchor_window: int) -> None:
        # Match strongest hits first so chip damage does not consume the best use events.
        dmg_order = sorted(
            range(len(mapped)),
            key=lambda idx: (-int(mapped[idx]["hp_diff_scaled"]), int(mapped[idx]["gt"])),
        )
        for idx in dmg_order:
            if mapped[idx]["source"] != "other":
                continue
            dmg_gt = int(mapped[idx]["gt"])
            candidates: list[tuple[int, int, int]] = []
            for use_idx, item in enumerate(use_items):
                if item["used"]:
                    continue
                use_gt = int(item["gt"])
                dt = dmg_gt - use_gt
                if dt < 0:
                    continue
                max_window = anchor_window if item["source"] == "anchor" else bed_window
                if dt > max_window:
                    continue
                # Prefer nearest use first, then the latest matching use tick.
                candidates.append((use_idx, dt, use_gt))
            if not candidates:
                continue
            best_use_idx, best_dt, best_use_gt = min(candidates, key=lambda c: (c[1], -c[2]))
            use_items[best_use_idx]["used"] = True
            mapped[idx]["source"] = str(use_items[best_use_idx]["source"])
            mapped[idx]["matched_use_gt"] = best_use_gt
            mapped[idx]["matched_dt"] = best_dt

    # Tight pass catches obvious direct matches; wide pass catches delayed explosions.
    _match_damage_events(
        bed_window=tight_bed_window_ticks,
        anchor_window=tight_anchor_window_ticks,
    )
    _match_damage_events(
        bed_window=bed_window_ticks,
        anchor_window=anchor_window_ticks,
    )

    bed_scaled = sum(int(ev["hp_diff_scaled"]) for ev in mapped if ev["source"] == "bed")
    anchor_scaled = sum(int(ev["hp_diff_scaled"]) for ev in mapped if ev["source"] == "anchor")
    other_scaled = sum(int(ev["hp_diff_scaled"]) for ev in mapped if ev["source"] == "other")
    return bed_scaled, anchor_scaled, other_scaled, mapped


def run_metrics_from_storage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with gzip.open(path, "rb") as f:
        nbt_file = nbtlib.File.parse(f)
    tracker, _ = _find_tracker(nbt_file)
    if not tracker:
        return {}
    samples, _ = _find_samples(nbt_file)
    last_sample_gt = int(samples[-1].get("gt", 0) or 0) if samples else 0
    sample_count = len(samples) if samples else 0
    run = tracker.get("run")
    if not isinstance(run, dict):
        return {}
    meta = tracker.get("meta") if isinstance(tracker.get("meta"), dict) else {}
    version = meta.get("version")
    deltas = run.get("deltas") if isinstance(run.get("deltas"), dict) else {}
    end_entry = run.get("end_entry") if isinstance(run.get("end_entry"), dict) else {}
    explosive_stand = run.get("explosive_stand") if isinstance(run.get("explosive_stand"), dict) else {}
    explode_events = _parse_explode_events(run.get("explode_events"))
    damage_events = _parse_damage_events(run.get("damage_events"))

    damage_scale = 100 if any(int(ev.get("hp_diff_scaled", 0) or 0) > 500 for ev in damage_events) else 1
    bed_from_events = sum(int(ev.get("bed_dmg_scaled", 0) or 0) for ev in damage_events)
    anchor_from_events = sum(int(ev.get("anchor_dmg_scaled", 0) or 0) for ev in damage_events)
    other_from_events = sum(int(ev.get("other_dmg_scaled", 0) or 0) for ev in damage_events)
    has_direct_split = (bed_from_events + anchor_from_events) > 0
    if has_direct_split:
        bed_damage_scaled = bed_from_events
        anchor_damage_scaled = anchor_from_events
        other_damage_scaled = other_from_events
        mapped_damage_events = []
        for ev in damage_events:
            bed_ev = int(ev.get("bed_dmg_scaled", 0) or 0)
            anchor_ev = int(ev.get("anchor_dmg_scaled", 0) or 0)
            other_ev = int(ev.get("other_dmg_scaled", 0) or 0)
            source = "other"
            if bed_ev > 0 and anchor_ev <= 0 and other_ev <= 0:
                source = "bed"
            elif anchor_ev > 0 and bed_ev <= 0 and other_ev <= 0:
                source = "anchor"
            elif other_ev > 0 and bed_ev <= 0 and anchor_ev <= 0:
                source = "other"
            elif bed_ev > 0 and anchor_ev > 0 and other_ev <= 0:
                source = "mixed_explosive"
            elif bed_ev > 0 and other_ev > 0 and anchor_ev <= 0:
                source = "mixed_bed_other"
            elif anchor_ev > 0 and other_ev > 0 and bed_ev <= 0:
                source = "mixed_anchor_other"
            elif bed_ev > 0 or anchor_ev > 0 or other_ev > 0:
                source = "mixed"
            mapped_damage_events.append(
                {
                    "gt": int(ev["gt"]),
                    "hp_diff_scaled": int(ev["hp_diff_scaled"]),
                    "explode_beds": int(ev.get("explode_beds", 0)),
                    "explode_anchors": int(ev.get("explode_anchors", 0)),
                    "near_bed_drop": int(ev.get("near_bed_drop", 0)),
                    "near_anchor_drop": int(ev.get("near_anchor_drop", 0)),
                    "bed_dmg_scaled": bed_ev,
                    "anchor_dmg_scaled": anchor_ev,
                    "other_dmg_scaled": other_ev,
                    "source": source,
                    "matched_use_gt": None,
                    "matched_dt": None,
                }
            )
    else:
        bed_damage_scaled, anchor_damage_scaled, other_damage_scaled, mapped_damage_events = _attribute_damage_from_events(
            explode_events,
            damage_events,
            tight_bed_window_ticks=24,
            tight_anchor_window_ticks=48,
            bed_window_ticks=220,
            anchor_window_ticks=320,
        )
    if damage_scale != 1:
        bed_damage_scaled = int(round(float(bed_damage_scaled) / float(damage_scale)))
        anchor_damage_scaled = int(round(float(anchor_damage_scaled) / float(damage_scale)))
        other_damage_scaled = int(round(float(other_damage_scaled) / float(damage_scale)))
        for ev in mapped_damage_events:
            ev["hp_diff_scaled"] = int(round(float(int(ev.get("hp_diff_scaled", 0))) / float(damage_scale)))
            ev["bed_dmg_scaled"] = int(round(float(int(ev.get("bed_dmg_scaled", 0))) / float(damage_scale)))
            ev["anchor_dmg_scaled"] = int(
                round(float(int(ev.get("anchor_dmg_scaled", 0))) / float(damage_scale))
            )
            ev["other_dmg_scaled"] = int(round(float(int(ev.get("other_dmg_scaled", 0))) / float(damage_scale)))

    entry_player_y = int(end_entry.get("player_y", 0) or 0)
    entry_top_y = int(end_entry.get("top_y", -1) or -1)
    entry_top_is_endstone = bool(end_entry.get("top_is_endstone", 0))
    entry_caged_endstone = entry_top_is_endstone and entry_top_y >= entry_player_y
    explosive_standing_logged = bool(explosive_stand.get("logged", 0))
    explosive_standing_y = int(explosive_stand.get("y", 0) or 0) if explosive_standing_logged else None

    return {
        "pack_version": str(version) if version is not None else "",
        "run_active": bool(run.get("active", 0)),
        "run_start_gt": int(run.get("start_gt", 0) or 0),
        "run_end_gt": int(run.get("end_gt", 0) or 0),
        "sample_count": sample_count,
        "last_sample_gt": last_sample_gt,
        "dragon_died": bool(run.get("dragon_died", 0)),
        "dragon_died_gt": int(run.get("dragon_died_gt", 0) or 0),
        "end_entry_logged": bool(end_entry.get("logged", 0)),
        "end_entry_gt": int(end_entry.get("gt", 0) or 0),
        "end_entry_player_y": entry_player_y,
        "end_entry_top_y": entry_top_y,
        "end_entry_top_is_endstone": entry_top_is_endstone,
        "end_entry_caged_endstone": entry_caged_endstone,
        "explosive_standing_logged": explosive_standing_logged,
        "explosive_standing_y": explosive_standing_y,
        "beds_exploded": int(deltas.get("beds_exploded", 0) or 0),
        "anchors_interactions": int(deltas.get("anchors_interactions", 0) or 0),
        "anchors_exploded_est": sum(int(ev.get("explode_anchors", 0) or 0) for ev in explode_events),
        "bows_shot": int(deltas.get("bows_shot", 0) or 0),
        "crossbows_shot": int(deltas.get("crossbows_shot", 0) or 0),
        "bed_damage_est": round(float(bed_damage_scaled), 2),
        "anchor_damage_est": round(float(anchor_damage_scaled), 2),
        "other_damage_est": round(float(other_damage_scaled), 2),
        "damage_events_count": len(damage_events),
        "explode_events_count": len(explode_events),
        "explode_events": explode_events,
        "damage_events": damage_events,
        "mapped_damage_events": mapped_damage_events,
    }


def parse_storage_file(
    path: Path,
    limit: int | None = None,
    reverse: bool = False,
    classify: bool = False,
    window_ticks: int = 600,
) -> int:
    if not path.exists():
        print(f"File not found: {path}")
        return 2

    with gzip.open(path, "rb") as f:
        nbt_file = nbtlib.File.parse(f)

    root = nbt_file
    samples, samples_path = _find_samples(root)
    if samples is None:
        print("No samples list found in this file.")
        print("Tip: ensure your storage key contains a `samples` list with {x,y,z,gt} entries.")
        return 1

    total = len(samples)
    print(f"Storage file: {path}")
    print(f"Samples path: {samples_path}")
    print(f"Sample count: {total}")

    rows = samples
    if reverse:
        rows = list(reversed(rows))
    if limit is not None and limit >= 0:
        rows = rows[:limit]

    for i, s in enumerate(rows):
        x = int(s.get("x", 0))
        y = int(s.get("y", 0))
        z = int(s.get("z", 0))
        gt = int(s.get("gt", 0))
        print(
            f"[{i}] gt={gt} x={x} y={y} z={z} "
            f"(scaled: x={x/1000:.3f}, y={y/1000:.3f}, z={z/1000:.3f})"
        )

    if classify:
        counts = _classify_node(samples, window_ticks=window_ticks)
        print("----")
        print(f"Node classification (first {window_ticks} ticks):")
        for key in ("back_diag", "front_diag", "back_straight", "front_straight"):
            print(f"{key}: {counts[key]}")
        dominant = max(counts.items(), key=lambda kv: kv[1])[0] if counts else None
        if dominant:
            print(f"dominant: {dominant}")

    return 0


def _bedrock_y_at(world_dir: Path, x: int, z: int) -> int | None:
    if anvil is None:
        return None
    region_dir = world_dir / "DIM1" / "region"
    if not region_dir.exists():
        return None
    cx = x // 16
    cz = z // 16
    rx = cx // 32
    rz = cz // 32
    region_path = region_dir / f"r.{rx}.{rz}.mca"
    if not region_path.exists():
        return None
    try:
        region = anvil.Region.from_file(str(region_path))
        local_cx = cx - (rx * 32)
        local_cz = cz - (rz * 32)
        chunk = region.get_chunk(local_cx, local_cz)
    except Exception:
        return None
    lx = x & 15
    lz = z & 15
    for y in range(255, -1, -1):
        try:
            block = chunk.get_block(lx, y, lz)
        except Exception:
            continue
        block_id = getattr(block, "id", None) or getattr(block, "name", None) or str(block)
        if "bedrock" in str(block_id):
            return y
    return None


def _bedrock_y_near(world_dir: Path, x: int, z: int, radius: int) -> int | None:
    best = None
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            y = _bedrock_y_at(world_dir, x + dx, z + dz)
            if y is None:
                continue
            if best is None or y > best:
                best = y
    return best


def bedrock_by_node(world_dir: Path, radius: int = 2) -> dict[str, int | None]:
    nodes = {
        "back_diag": (-34, 24),
        "front_diag": (33, -25),
        "back_straight": (-42, -1),
        "front_straight": (42, 0),
    }
    return {name: _bedrock_y_near(world_dir, x, z, radius=radius) for name, (x, z) in nodes.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse Minecraft command_storage_*.dat and log sampled dragon positions.")
    parser.add_argument(
        "path",
        nargs="?",
        default=r"C:\Users\Boyen\Desktop\MultiMC\instances\ZeroPractice\.minecraft\saves\Random Speedrun #2346247\data\command_storage_zdash.dat",
        help="Path to command_storage_zdash.dat",
    )
    parser.add_argument(
        "--world",
        help="World folder. If set, uses <world>/data/command_storage_zdash.dat and reads bedrock from DIM1.",
    )
    parser.add_argument("--limit", type=int, default=50, help="How many sample rows to print (default: 50).")
    parser.add_argument("--all", action="store_true", help="Print all rows.")
    parser.add_argument("--reverse", action="store_true", help="Print newest-first.")
    parser.add_argument("--classify", action="store_true", help="Classify samples to the 4 node positions.")
    parser.add_argument("--window-ticks", type=int, default=600, help="Only classify samples in first N ticks.")
    parser.add_argument("--bedrock-radius", type=int, default=2, help="Search radius around node coords.")
    args = parser.parse_args()

    limit = None if args.all else args.limit
    path = Path(args.path)
    world_dir = Path(args.world) if args.world else None
    if world_dir is not None:
        path = world_dir / "data" / "command_storage_zdash.dat"
    result = parse_storage_file(
        path,
        limit=limit,
        reverse=args.reverse,
        classify=args.classify,
        window_ticks=args.window_ticks,
    )
    if world_dir is not None:
        print("----")
        print(f"World: {world_dir}")
        nodes = {
            "back_diag": (-34, 24),
            "front_diag": (33, -25),
            "back_straight": (-42, -1),
            "front_straight": (42, 0),
        }
        if anvil is None:
            print("Bedrock lookup: skipped (install anvil-parser).")
        else:
            print("Bedrock Y at node coords:")
            for name, (x, z) in nodes.items():
                y = _bedrock_y_near(world_dir, x, z, radius=args.bedrock_radius)
                print(f"{name} @ ({x}, {z}) r={args.bedrock_radius} -> y={y}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
