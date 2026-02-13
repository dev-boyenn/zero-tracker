from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = PROJECT_ROOT / "app" / "static"

DEFAULT_LOG_PATH = r"C:\Users\Boyen\Desktop\MultiMC\instances\Ranked\.minecraft\logs\latest.log"
LOG_PATH = Path(os.getenv("ZERO_DASH_LOG_PATH", DEFAULT_LOG_PATH))
DB_PATH = Path(os.getenv("ZERO_DASH_DB_PATH", str(DATA_DIR / "zero_cycles.db")))

POLL_SECONDS = float(os.getenv("ZERO_DASH_POLL_SECONDS", "0.4"))
MAJOR_DAMAGE_THRESHOLD = int(os.getenv("ZERO_DASH_MAJOR_DAMAGE_THRESHOLD", "15"))
