from __future__ import annotations

import csv
import json
import re
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import (
    MPK_BACK_DIAG_JSON_PATH,
    MPK_BACK_DIAG_TSV_PATH,
    MPK_FRONT_DIAG_JSON_PATH,
    MPK_FRONT_DIAG_TSV_PATH,
)

TOWER_BY_HEIGHT = {
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

HEIGHT_RE = re.compile(r"(\d+)")


def _parse_float(text: str) -> float | None:
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int_prefix(text: str) -> int | None:
    match = HEIGHT_RE.search(str(text or ""))
    if not match:
        return None
    return int(match.group(1))


def _parse_table(tsv_path: Path, side: str) -> dict[str, object]:
    rows: list[list[str]] = []
    with tsv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            rows.append(row)
    if not rows:
        return {"side": side, "entries": [], "source": str(tsv_path)}

    header = [str(c or "").strip().strip('"') for c in rows[0]]
    level_cols: list[tuple[int, int]] = []
    for idx, name in enumerate(header):
        n = name.lower().strip()
        if n == "open":
            level_cols.append((48, idx))
            continue
        if n.startswith("o"):
            try:
                level_cols.append((int(n[1:]), idx))
            except ValueError:
                pass

    entries: list[dict[str, object]] = []
    current_tower_height: int | None = None
    for row in rows[1:]:
        if not row:
            continue
        if len(row) < 3:
            continue
        tower_cell = str(row[0] or "").strip()
        if tower_cell:
            current_tower_height = _parse_int_prefix(tower_cell)
        if current_tower_height is None:
            continue
        tower_name = TOWER_BY_HEIGHT.get(current_tower_height)
        if not tower_name:
            continue
        standing = _parse_int_prefix(row[1] if len(row) > 1 else "")
        if standing is None:
            continue

        for o_level, col_idx in level_cols:
            if col_idx >= len(row):
                continue
            value = _parse_float(row[col_idx])
            if value is None:
                continue
            entries.append(
                {
                    "tower_name": tower_name,
                    "tower_height": current_tower_height,
                    "side": side,
                    "standing_height": standing,
                    "o_level": int(o_level),
                    "leniency": float(value),
                }
            )

    return {"side": side, "entries": entries, "source": str(tsv_path)}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    front = _parse_table(MPK_FRONT_DIAG_TSV_PATH, "Front")
    back = _parse_table(MPK_BACK_DIAG_TSV_PATH, "Back")
    _write_json(MPK_FRONT_DIAG_JSON_PATH, front)
    _write_json(MPK_BACK_DIAG_JSON_PATH, back)
    print(f"Wrote {MPK_FRONT_DIAG_JSON_PATH} ({len(front['entries'])} entries)")
    print(f"Wrote {MPK_BACK_DIAG_JSON_PATH} ({len(back['entries'])} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
