from __future__ import annotations

import argparse
import re
from pathlib import Path
import subprocess
import sys
import time


_SAVE_WORLD_RE = re.compile(r"ServerLevel\[(?P<name>[^\]]+)\]")
_CACHE_WORLD_RE = re.compile(r"Cached options for '(?P<name>[^']+)'")
_F3C_TP_RE = re.compile(
    r"^/execute\s+in\s+(?P<dim>[a-z0-9_:\.-]+)\s+run\s+tp\s+@s\s+"
    r"(?P<x>-?\d+(?:\.\d+)?)\s+"
    r"(?P<y>-?\d+(?:\.\d+)?)\s+"
    r"(?P<z>-?\d+(?:\.\d+)?)\s+"
    r"(?P<yaw>-?\d+(?:\.\d+)?)\s+"
    r"(?P<pitch>-?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def _extract_world_name_hint(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    if "Saving chunks for level" in line:
        m = _SAVE_WORLD_RE.search(line)
        if m:
            return m.group("name").strip()
    if "Cached options for '" in line:
        m = _CACHE_WORLD_RE.search(line)
        if m:
            return m.group("name").strip()
    return None


def _is_waiting_exit_line(raw_line: str) -> bool:
    return "StateOutput State: waiting" in raw_line.strip()


def _is_world_exit_line(raw_line: str) -> bool:
    body = raw_line.strip()
    if not body:
        return False
    if "Stopping!" in body:
        return True
    if _is_waiting_exit_line(raw_line):
        return True
    lower = body.lower()
    if "disconnecting from server" in lower:
        return True
    if "left the game" in lower:
        return True
    return False


def _find_latest_world(saves_dir: Path) -> Path | None:
    worlds = [p for p in saves_dir.iterdir() if p.is_dir()]
    if not worlds:
        return None
    return max(worlds, key=lambda p: p.stat().st_mtime)


def _run_analyzer_once(
    analyzer_path: Path,
    world_path: Path,
    *,
    update_db: bool,
    rebuild: bool,
    aim_lines: list[str],
    timing: bool,
) -> tuple[int, str]:
    cmd = [sys.executable, str(analyzer_path), str(world_path)]
    if update_db:
        cmd.append("--update-db")
    if rebuild:
        cmd.append("--rebuild")
    for raw_line in aim_lines:
        line = str(raw_line or "").strip()
        if line:
            cmd.extend(["--aim-line", line])
    if timing:
        cmd.append("--timing")
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
    return int(proc.returncode), output


def _analyze_with_retry(
    analyzer_path: Path,
    world_path: Path,
    *,
    update_db: bool,
    rebuild: bool,
    aim_lines: list[str],
    timing: bool,
    retry_seconds: float,
    retry_interval: float,
) -> bool:
    deadline = time.time() + max(0.0, retry_seconds)
    attempt = 0
    while True:
        attempt += 1
        code, output = _run_analyzer_once(
            analyzer_path,
            world_path,
            update_db=update_db,
            rebuild=rebuild if attempt == 1 else False,
            aim_lines=aim_lines,
            timing=timing,
        )
        print(f"[analyze attempt {attempt}] world={world_path.name} exit={code}")
        if output:
            print(output)
        if code == 0:
            return True
        now = time.time()
        if now >= deadline:
            return False
        time.sleep(max(0.1, retry_interval))


def _read_clipboard_text_windows() -> str | None:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Clipboard -Raw",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1.2,
        )
    except Exception:
        return None
    if int(proc.returncode) != 0:
        return None
    return str(proc.stdout or "").strip()


def _parse_f3c_tp_line(line: str | None) -> dict[str, float | str] | None:
    raw = str(line or "").strip()
    if not raw:
        return None
    m = _F3C_TP_RE.match(raw)
    if not m:
        return None
    try:
        return {
            "raw": raw,
            "dim": str(m.group("dim")).lower(),
            "x": float(m.group("x")),
            "y": float(m.group("y")),
            "z": float(m.group("z")),
            "yaw": float(m.group("yaw")),
            "pitch": float(m.group("pitch")),
        }
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch latest.log and auto-run stronghold analyzer for latest world on exit."
    )
    parser.add_argument(
        "--instance",
        default=r"C:\Users\Boyen\Desktop\MultiMC\instances\Ranked\.minecraft",
        help="Path to .minecraft instance folder.",
    )
    parser.add_argument(
        "--retry-seconds",
        type=float,
        default=25.0,
        help="How long to retry analysis after exit (storage write delay handling).",
    )
    parser.add_argument(
        "--retry-interval",
        type=float,
        default=1.2,
        help="Seconds between retry attempts.",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Pass --update-db to analyze_stronghold_world.py.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Pass --rebuild on first analyze attempt after each exit.",
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Pass --timing to analyze_stronghold_world.py for phase timing diagnostics.",
    )
    args = parser.parse_args()

    root = Path(args.instance)
    log_path = root / "logs" / "latest.log"
    saves_dir = root / "saves"
    analyzer_path = Path(__file__).resolve().parent / "analyze_stronghold_world.py"
    if not analyzer_path.exists():
        print(f"Analyzer script not found: {analyzer_path}")
        return 2
    if not log_path.exists():
        print(f"Log not found: {log_path}")
        return 2
    if not saves_dir.exists():
        print(f"Saves dir not found: {saves_dir}")
        return 2

    last_processed_world = ""
    last_world_hint = ""
    last_world_hint_ts = 0.0
    last_clip_poll_ts = 0.0
    last_clip_raw = ""
    latest_aim_lines: list[str] = []
    latest_aim_seen: set[str] = set()
    position = log_path.stat().st_size
    print(f"Watching log: {log_path}")
    print(f"Saves dir: {saves_dir}")
    while True:
        try:
            now = time.time()
            if (now - last_clip_poll_ts) >= 0.9:
                last_clip_poll_ts = now
                clip_raw = _read_clipboard_text_windows()
                if clip_raw and clip_raw != last_clip_raw:
                    last_clip_raw = clip_raw
                    parsed = _parse_f3c_tp_line(clip_raw)
                    if parsed is not None:
                        raw = str(parsed["raw"])
                        if raw not in latest_aim_seen:
                            latest_aim_seen.add(raw)
                            latest_aim_lines.append(raw)
                            print(
                                "[clip] captured F3+C aim: "
                                + f"x={float(parsed['x']):.2f} y={float(parsed['y']):.2f} z={float(parsed['z']):.2f} "
                                + f"yaw={float(parsed['yaw']):.2f} dim={parsed['dim']} "
                                + f"(n={len(latest_aim_lines)})"
                            )
            size = log_path.stat().st_size
            if size < position:
                position = 0
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                line = handle.readline()
                if not line:
                    position = handle.tell()
                    time.sleep(0.35)
                    continue
                position = handle.tell()
                world_hint = _extract_world_name_hint(line)
                if world_hint:
                    last_world_hint = world_hint
                    last_world_hint_ts = time.time()
                if not _is_world_exit_line(line):
                    continue
                world: Path | None = None
                # For fast reset workflows, 'waiting' is followed immediately by creating a new world.
                # Pin analysis to the just-saved world hinted in logs to avoid hopping to the next world.
                if _is_waiting_exit_line(line):
                    if last_world_hint and (time.time() - last_world_hint_ts) <= 30.0:
                        hinted = saves_dir / last_world_hint
                        if hinted.exists() and hinted.is_dir():
                            world = hinted
                    if world is None:
                        # No reliable world hint for waiting state: skip rather than mis-analyzing new world.
                        continue
                else:
                    # Hard exits can still use hint first, then latest-world fallback.
                    if last_world_hint and (time.time() - last_world_hint_ts) <= 30.0:
                        hinted = saves_dir / last_world_hint
                        if hinted.exists() and hinted.is_dir():
                            world = hinted
                    if world is None:
                        world = _find_latest_world(saves_dir)
                if world is None:
                    print("World exit detected, but no worlds were found.")
                    continue
                if world.name == last_processed_world:
                    continue
                print(f"World exit detected. Auto-analyzing latest world: {world}")
                ok = _analyze_with_retry(
                    analyzer_path,
                    world,
                    update_db=bool(args.update_db),
                    rebuild=bool(args.rebuild),
                    aim_lines=latest_aim_lines,
                    timing=bool(args.timing),
                    retry_seconds=float(args.retry_seconds),
                    retry_interval=float(args.retry_interval),
                )
                if ok:
                    last_processed_world = world.name
                    last_world_hint = ""
                    last_world_hint_ts = 0.0
                    latest_aim_lines = []
                    latest_aim_seen = set()
                    print(f"[done] analyzed {world.name}")
                else:
                    latest_aim_lines = []
                    latest_aim_seen = set()
                    print(f"[failed] could not analyze {world.name} within retry window")
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
