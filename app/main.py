from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import time
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .attempt_tracker import AttemptTracker
from config import DB_PATH, LOG_PATH, POLL_SECONDS, STATIC_DIR
from .database import Database
from .log_watcher import (
    STATE_FILE_IDENTITY,
    STATE_FILE_POSITION,
    STATE_LAST_HEARTBEAT,
    LogWatcher,
)
from .metrics import build_dashboard_payload_selected, compute_recent_attempts


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(DB_PATH)
    tracker = AttemptTracker(db)
    watcher = LogWatcher(log_path=LOG_PATH, poll_seconds=POLL_SECONDS, db=db, tracker=tracker)
    watcher.start()

    app.state.db = db
    app.state.watcher = watcher
    app.state.started_at = utc_now()
    app.state.dashboard_cache = {}
    try:
        yield
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)
        db.close()


app = FastAPI(title="Zero Cycle Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    return {
        "ok": True,
        "started_at": request.app.state.started_at,
        "now": utc_now(),
        "log_path": str(LOG_PATH),
        "db_path": str(DB_PATH),
        "poll_seconds": POLL_SECONDS,
        "log_exists": LOG_PATH.exists(),
        "reader_identity": db.get_state(STATE_FILE_IDENTITY, ""),
        "reader_position": int(db.get_state(STATE_FILE_POSITION, "0") or "0"),
        "last_heartbeat_utc": db.get_state(STATE_LAST_HEARTBEAT, ""),
    }


def _normalize_filter(
    include_1_8: bool,
    rotation: str,
    window: str,
    tower: str | None,
    side: str | None,
    detail: str,
) -> tuple[bool, str, str, str | None, str | None, str]:
    rotation_norm = (rotation or "both").strip().lower()
    if rotation_norm not in {"both", "cw", "ccw"}:
        rotation_norm = "both"
    window_norm = (window or "all").strip().lower()
    if window_norm not in {"all", "current_session", "last_10", "last_25", "last_50", "last_100"}:
        window_norm = "all"
    detail_norm = (detail or "full").strip().lower()
    if detail_norm not in {"light", "full"}:
        detail_norm = "full"
    tower_norm = None if tower in {None, "", "__GLOBAL__"} else str(tower)
    side_norm = None if side in {None, "", "__GLOBAL__"} else str(side)
    if side_norm not in {None, "Front", "Back"}:
        side_norm = None
    return (bool(include_1_8), rotation_norm, window_norm, tower_norm, side_norm, detail_norm)


def _build_dashboard_payload_cached(
    request: Request,
    *,
    include_1_8: bool,
    rotation: str,
    window: str,
    tower: str | None,
    side: str | None,
    detail: str,
) -> dict[str, Any]:
    db: Database = request.app.state.db
    cache: dict[Any, dict[str, Any]] = request.app.state.dashboard_cache
    include_1_8, rotation, window, tower, side, detail = _normalize_filter(
        include_1_8, rotation, window, tower, side, detail
    )
    data_version_row = db.query_one("PRAGMA data_version")
    data_version = int(data_version_row[0]) if data_version_row is not None else 0
    max_attempt_row = db.query_one("SELECT COALESCE(MAX(id), 0) AS max_id FROM attempts")
    max_attempt_id = int(max_attempt_row["max_id"]) if max_attempt_row is not None else 0
    cache_key = (detail, include_1_8, rotation, window, tower, side, data_version, max_attempt_id)
    now = time.time()
    entry = cache.get(cache_key)
    if entry is not None and float(entry.get("expires_at", 0.0)) > now:
        payload = dict(entry["payload"])
    else:
        payload = build_dashboard_payload_selected(
            db,
            include_1_8=include_1_8,
            rotation=rotation,
            window=window,
            tower_name=tower,
            front_back=side,
            detail=detail,
        )
        ttl = 0.4 if detail == "light" else 1.2
        cache[cache_key] = {"payload": payload, "expires_at": now + ttl}
        if len(cache) > 128:
            stale_keys = [k for k, v in cache.items() if float(v.get("expires_at", 0.0)) <= now]
            for k in stale_keys[:64]:
                cache.pop(k, None)
    payload["server_time_utc"] = utc_now()
    payload["log_path"] = str(LOG_PATH)
    payload["db_path"] = str(DB_PATH)
    return payload


@app.get("/api/dashboard")
def dashboard(
    request: Request,
    include_1_8: bool = Query(default=False),
    rotation: str = Query(default="both"),
    window: str = Query(default="all"),
    tower: str | None = Query(default=None),
    side: str | None = Query(default=None),
    detail: str = Query(default="full"),
) -> dict[str, object]:
    return _build_dashboard_payload_cached(
        request,
        include_1_8=include_1_8,
        rotation=rotation,
        window=window,
        tower=tower,
        side=side,
        detail=detail,
    )


@app.get("/api/stream")
def stream(
    request: Request,
    include_1_8: bool = Query(default=False),
    rotation: str = Query(default="both"),
    window: str = Query(default="all"),
    tower: str | None = Query(default=None),
    side: str | None = Query(default=None),
    detail: str = Query(default="light"),
) -> StreamingResponse:

    def event_stream():
        while True:
            payload = _build_dashboard_payload_cached(
                request,
                include_1_8=include_1_8,
                rotation=rotation,
                window=window,
                tower=tower,
                side=side,
                detail=detail,
            )
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/recent-attempts")
def recent_attempts(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, object]:
    db: Database = request.app.state.db
    return {"attempts": compute_recent_attempts(db, limit=limit)}


@app.get("/api/raw-events")
def raw_events(
    request: Request, limit: int = Query(default=200, ge=1, le=2000)
) -> dict[str, object]:
    db: Database = request.app.state.db
    rows = db.query_all(
        """
        SELECT
            id,
            ingested_at_utc,
            clock_time,
            thread_name,
            level,
            source,
            is_chat,
            chat_message,
            raw_line,
            file_offset
        FROM raw_log_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return {"events": [dict(row) for row in rows]}
