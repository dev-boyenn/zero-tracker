from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = PROJECT_ROOT / "app" / "static"

DB_PATH = Path(os.getenv("ZERO_DASH_DB_PATH", str(DATA_DIR / "zero_cycles.db")))

MPK_ENABLED = os.getenv("ZERO_DASH_MPK_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
MPK_SEEDS_MAP_PATH = Path(
    os.getenv(
        "ZERO_DASH_MPK_SEEDS_MAP_PATH",
        str(PROJECT_ROOT / "seedsMap.json"),
    )
)
MPK_FRONT_DIAG_TSV_PATH = Path(
    os.getenv(
        "ZERO_DASH_MPK_FRONT_DIAG_TSV_PATH",
        str(PROJECT_ROOT / "front_diag.tsv"),
    )
)
MPK_BACK_DIAG_TSV_PATH = Path(
    os.getenv(
        "ZERO_DASH_MPK_BACK_DIAG_TSV_PATH",
        str(PROJECT_ROOT / "back_diag.tsv"),
    )
)
MPK_FRONT_DIAG_JSON_PATH = Path(
    os.getenv(
        "ZERO_DASH_MPK_FRONT_DIAG_JSON_PATH",
        str(DATA_DIR / "leniency" / "front_diag.json"),
    )
)
MPK_BACK_DIAG_JSON_PATH = Path(
    os.getenv(
        "ZERO_DASH_MPK_BACK_DIAG_JSON_PATH",
        str(DATA_DIR / "leniency" / "back_diag.json"),
    )
)

POLL_SECONDS = float(os.getenv("ZERO_DASH_POLL_SECONDS", "0.4"))
MAJOR_DAMAGE_THRESHOLD = int(os.getenv("ZERO_DASH_MAJOR_DAMAGE_THRESHOLD", "15"))
