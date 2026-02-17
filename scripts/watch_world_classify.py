from __future__ import annotations

import argparse
import time
from pathlib import Path

from parse_command_storage import (
    bedrock_by_node,
    dominant_node_from_storage,
    rotation_from_storage,
    run_metrics_from_storage,
)


def _find_latest_world(saves_dir: Path) -> Path | None:
    worlds = [p for p in saves_dir.iterdir() if p.is_dir()]
    if not worlds:
        return None
    return max(worlds, key=lambda p: p.stat().st_mtime)


def _is_world_exit_line(line: str) -> bool:
    lower = line.lower()
    return (
        "disconnecting from server" in lower
        or "left the game" in lower
    )


def _wait_for_storage(path: Path, timeout_s: float = 20.0, stable_s: float = 1.5) -> bool:
    deadline = time.time() + timeout_s
    last_size = -1
    stable_since = 0.0
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            size = path.stat().st_size
            if size == last_size:
                if stable_since == 0.0:
                    stable_since = time.time()
                if (time.time() - stable_since) >= stable_s:
                    return True
            else:
                stable_since = 0.0
            last_size = size
        time.sleep(0.25)
    return False


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


def _fmt_value(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_damage_events(events: list[dict[str, object]], limit: int = 40) -> str:
    if not events:
        return "none"
    tail = events[-limit:]
    parts: list[str] = []
    for e in tail:
        gt = int(e.get("gt", 0) or 0)
        hp = float(int(e.get("hp_diff_scaled", 0) or 0))
        source = str(e.get("source", "other"))
        parts.append(f"  gt={gt} dmg={_fmt_value(hp)} type={source}")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch latest.log and classify last world on exit.")
    parser.add_argument(
        "--instance",
        default=r"C:\Users\Boyen\Desktop\MultiMC\instances\Ranked\.minecraft",
        help="Path to .minecraft for the instance.",
    )
    parser.add_argument(
        "--window-ticks",
        type=int,
        default=600,
        help="Classification window (ticks from first sample).",
    )
    args = parser.parse_args()

    root = Path(args.instance)
    log_path = root / "logs" / "latest.log"
    saves_dir = root / "saves"
    if not log_path.exists():
        print(f"Log not found: {log_path}")
        return 2
    if not saves_dir.exists():
        print(f"Saves dir not found: {saves_dir}")
        return 2

    # Tail the log file.
    position = log_path.stat().st_size
    print(f"Watching: {log_path}")
    while True:
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                line = handle.readline()
                if line:
                    position = handle.tell()
                    if _is_world_exit_line(line):
                        world = _find_latest_world(saves_dir)
                        if world is None:
                            print("No worlds found.")
                            continue
                        print(f"World exit detected. Classifying: {world}")
                        data_dir = world / "data"
                        storage_path = _find_storage_file(data_dir)
                        if storage_path is None:
                            print(f"No command_storage file found yet in: {data_dir}")
                            continue
                        if not _wait_for_storage(storage_path, timeout_s=20.0):
                            print(f"Storage not ready yet: {storage_path}")
                            continue
                        node, _ = dominant_node_from_storage(storage_path, window_ticks=args.window_ticks)
                        if node is None:
                            print("tower_height: unknown (no samples)")
                            continue
                        beds = bedrock_by_node(world, radius=4)
                        height = beds.get(node)
                        rotation = rotation_from_storage(storage_path, window_ticks=args.window_ticks)
                        metrics = run_metrics_from_storage(storage_path)
                        print("Classification:")
                        print(f"  Node/Type: {node} | tower_height={height} | rotation={rotation}")
                        print(
                            "  Run: "
                            + " | ".join(
                                [
                                    f"pack={metrics.get('pack_version', '') or 'unknown'}",
                                    f"active={metrics.get('run_active', False)}",
                                    f"dragon_died={metrics.get('dragon_died', False)}",
                                    f"died_gt={metrics.get('dragon_died_gt', 0)}",
                                    f"flyaway={metrics.get('flyaway_detected', False)}",
                                    f"flyaway_gt={metrics.get('flyaway_detected_gt', 0)}",
                                    f"flyaway_x={metrics.get('flyaway_dragon_x', 0)}",
                                    f"flyaway_y={metrics.get('flyaway_dragon_y', 0)}",
                                    f"flyaway_z={metrics.get('flyaway_dragon_z', 0)}",
                                    f"flyaway_crystals={metrics.get('flyaway_crystals_alive', -1)}",
                                ]
                            )
                        )
                        print(
                            "  End Entry: "
                            + " | ".join(
                                [
                                    f"logged={metrics.get('end_entry_logged', False)}",
                                    f"gt={metrics.get('end_entry_gt', 0)}",
                                    f"player_y={metrics.get('end_entry_player_y', 0)}",
                                    f"top_y={metrics.get('end_entry_top_y', -1)}",
                                    f"top_is_endstone={metrics.get('end_entry_top_is_endstone', False)}",
                                    f"caged_endstone={metrics.get('end_entry_caged_endstone', False)}",
                                    f"explosive_standing_y={metrics.get('explosive_standing_y', None)}",
                                ]
                            )
                        )
                        print(
                            "  Totals: "
                            + " | ".join(
                                [
                                    f"beds_from_damage={metrics.get('beds_exploded', 0)}",
                                    f"anchors_from_damage={metrics.get('anchors_exploded_est', 0)}",
                                    (
                                        "explosives="
                                        + str(metrics.get("explosives_base_count", 0))
                                        + (
                                            f"+{metrics.get('explosives_plus_one_count', 0)}"
                                            if int(metrics.get("explosives_plus_one_count", 0) or 0) > 0
                                            else ""
                                        )
                                    ),
                                    f"bows_shot={metrics.get('bows_shot', 0)}",
                                    f"crossbows_shot={metrics.get('crossbows_shot', 0)}",
                                ]
                            )
                        )
                        print(
                            "  Damage: "
                            + " | ".join(
                                [
                                    f"bed={_fmt_value(float(metrics.get('bed_damage_est', 0) or 0))}",
                                    f"anchor={_fmt_value(float(metrics.get('anchor_damage_est', 0) or 0))}",
                                    f"other={_fmt_value(float(metrics.get('other_damage_est', 0) or 0))}",
                                    f"events={metrics.get('damage_events_count', 0)}",
                                    f"explode_events={metrics.get('explode_events_count', 0)}",
                                ]
                            )
                        )
                        formatted_damage = _format_damage_events(
                            metrics.get("mapped_damage_events", []),
                        )
                        if formatted_damage == "none":
                            print("damage_log: none")
                        else:
                            print("damage_log:")
                            print(formatted_damage)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
