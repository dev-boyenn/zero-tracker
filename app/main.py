from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import DB_PATH, MPK_ENABLED, POLL_SECONDS, PROJECT_ROOT, STATIC_DIR
from .database import Database
from .log_watcher import LogWatcher
from .metrics import (
    ATTEMPT_SOURCE_CTX,
    build_dashboard_payload_selected,
    clear_runtime_atum_seed,
    compute_recent_attempts,
    is_mpk_full_random_override_enabled,
    replace_current_mpk_weak_lock_with_open,
    skip_current_mpk_weak_lock,
    set_mpk_full_random_override,
)
from .metrics import (
    get_mpk_locked_targets,
    parse_mpk_target_key,
    set_mpk_locked_targets,
    toggle_mpk_locked_target,
)
from .mpk_injection import MpkInjectionToken, MpkInjector, MpkRuntimePaths

try:
    from .mpk_attempt_tracker import MpkAttemptTracker
except ModuleNotFoundError:
    MpkAttemptTracker = None  # type: ignore[assignment]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


STRONGHOLD_MAPS_DIR = PROJECT_ROOT / "data" / "stronghold_maps"


class MpkSetupRequest(BaseModel):
    path: str
    open_recipe_book_on_run_start: bool | None = None
    enable_dragon_node_patch: bool | None = None
    legal_ranked_instance: bool | None = None


def _configured_mpk_path(db: Database) -> Path | None:
    saved = (db.get_state("setup.mpk_instance_path", "") or "").strip()
    if saved:
        return Path(saved)
    return None


def _clear_saved_mpk_path(db: Database) -> None:
    db.execute("DELETE FROM ingest_state WHERE key = ?", ("setup.mpk_instance_path",))


def _state_bool(db: Database, key: str, default: bool) -> bool:
    raw = (db.get_state(key, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _configured_recipe_book_enabled(db: Database) -> bool:
    return _state_bool(db, "setup.inject_recipe_book", True)


def _configured_dragon_patch_enabled(db: Database) -> bool:
    return _state_bool(db, "setup.inject_dragon_patch", True)


def _configured_legal_ranked_instance(db: Database) -> bool:
    return _state_bool(db, "setup.legal_ranked_instance", False)


def _stop_mpk_runtime(app: FastAPI, *, revert_injection: bool = True) -> None:
    mpk_watcher: LogWatcher | None = getattr(app.state, "mpk_watcher", None)
    if mpk_watcher is not None:
        mpk_watcher.stop()
        mpk_watcher.join(timeout=2.0)
    app.state.mpk_watcher = None

    token: MpkInjectionToken | None = getattr(app.state, "mpk_injection_token", None)
    injector: MpkInjector | None = getattr(app.state, "mpk_injector", None)
    app.state.mpk_injection_token = None
    app.state.mpk_injected = False
    if revert_injection and token is not None and injector is not None:
        revert_error = injector.revert(token)
        if revert_error:
            app.state.mpk_setup_error = revert_error

    app.state.mpk_runtime = None


def _start_mpk_runtime(
    app: FastAPI,
    db: Database,
    minecraft_dir: Path,
    *,
    inject_recipe_book: bool,
    inject_dragon_patch: bool,
    legal_ranked_instance: bool,
) -> bool:
    injector: MpkInjector = app.state.mpk_injector
    runtime = injector.runtime_from_minecraft_dir(minecraft_dir)
    token, inject_error = injector.apply(
        runtime,
        inject_recipe_book=inject_recipe_book,
        inject_dragon_patch=inject_dragon_patch,
        inject_atum=not legal_ranked_instance,
        disable_ranked_mods=not legal_ranked_instance,
    )
    if token is None:
        app.state.mpk_setup_required = True
        app.state.mpk_setup_error = inject_error or "MPK injection failed."
        app.state.mpk_runtime = None
        app.state.mpk_watcher = None
        app.state.mpk_injected = False
        app.state.mpk_injection_token = None
        return False

    mpk_tracker = MpkAttemptTracker(db=db, saves_dir=runtime.saves_dir)
    mpk_watcher = LogWatcher(
        log_path=runtime.log_path,
        poll_seconds=POLL_SECONDS,
        db=db,
        tracker=mpk_tracker,
        state_prefix="mpk_log_reader",
    )
    mpk_watcher.start()

    db.set_state("setup.mpk_instance_path", str(runtime.minecraft_dir))
    app.state.mpk_runtime = runtime
    app.state.mpk_watcher = mpk_watcher
    app.state.mpk_injected = True
    app.state.mpk_injection_token = token
    app.state.mpk_setup_required = False
    app.state.mpk_setup_error = ""
    if legal_ranked_instance:
        # Legal mode always runs with full random and no forced set-seed injection.
        set_mpk_full_random_override(db, True)
    return True


def _init_mpk_runtime(app: FastAPI, db: Database) -> None:
    if not bool(getattr(app.state, "mpk_enabled", False)):
        app.state.mpk_setup_required = False
        app.state.mpk_setup_error = ""
        return
    injector: MpkInjector = app.state.mpk_injector
    configured = _configured_mpk_path(db)
    if configured is None:
        app.state.mpk_setup_required = True
        app.state.mpk_setup_error = "No MPK instance path configured yet."
        app.state.mpk_runtime = None
        app.state.mpk_watcher = None
        app.state.mpk_injected = False
        app.state.mpk_injection_token = None
        return
    normalized, path_error = injector.normalize_instance_path(configured)
    if normalized is None:
        app.state.mpk_setup_required = True
        app.state.mpk_setup_error = path_error or "MPK instance path is invalid."
        app.state.mpk_runtime = None
        app.state.mpk_watcher = None
        app.state.mpk_injected = False
        app.state.mpk_injection_token = None
        return
    _start_mpk_runtime(
        app,
        db,
        normalized,
        inject_recipe_book=(
            False if _configured_legal_ranked_instance(db) else _configured_recipe_book_enabled(db)
        ),
        inject_dragon_patch=(
            False if _configured_legal_ranked_instance(db) else _configured_dragon_patch_enabled(db)
        ),
        legal_ranked_instance=_configured_legal_ranked_instance(db),
    )


def _runtime_health_payload(app: FastAPI, db: Database) -> dict[str, object]:
    mpk_identity_key = "mpk_log_reader.file_identity"
    mpk_position_key = "mpk_log_reader.file_position"
    mpk_heartbeat_key = "mpk_log_reader.last_heartbeat_utc"
    runtime: MpkRuntimePaths | None = getattr(app.state, "mpk_runtime", None)
    configured = _configured_mpk_path(db)
    mpk_instance_path = ""
    mpk_log_path: Path | None = None
    mpk_saves_dir: Path | None = None
    if runtime is not None:
        mpk_instance_path = str(runtime.minecraft_dir)
        mpk_log_path = runtime.log_path
        mpk_saves_dir = runtime.saves_dir
    elif configured is not None:
        mpk_instance_path = str(configured)
        injector: MpkInjector = app.state.mpk_injector
        normalized, _ = injector.normalize_instance_path(configured)
        if normalized is not None:
            fallback = injector.runtime_from_minecraft_dir(normalized)
            mpk_log_path = fallback.log_path
            mpk_saves_dir = fallback.saves_dir
    injector: MpkInjector = app.state.mpk_injector
    effective_runtime = runtime
    if effective_runtime is None and configured is not None:
        normalized, _ = injector.normalize_instance_path(configured)
        if normalized is not None:
            effective_runtime = injector.runtime_from_minecraft_dir(normalized)

    legal_ranked_instance = _configured_legal_ranked_instance(db)
    recipe_book_enabled = (
        False if legal_ranked_instance else _configured_recipe_book_enabled(db)
    )
    dragon_patch_enabled = (
        False if legal_ranked_instance else _configured_dragon_patch_enabled(db)
    )
    injected_components: list[dict[str, object]] = []
    if effective_runtime is not None:
        atum_mod_path = ""
        try:
            atum_mods = sorted(
                (
                    p
                    for p in effective_runtime.mods_dir.glob("atum*.jar")
                    if p.is_file()
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if atum_mods:
                atum_mod_path = str(atum_mods[0])
        except Exception:
            atum_mod_path = ""

        atum_json_enabled = False
        atum_json_seed = ""
        try:
            if effective_runtime.atum_json_path.exists():
                payload = json.loads(effective_runtime.atum_json_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    atum_json_seed = str(payload.get("seed", "") or "")
                    data_pack_settings = payload.get("dataPackSettings")
                    if isinstance(data_pack_settings, dict):
                        enabled = data_pack_settings.get("enabled")
                        if isinstance(enabled, list):
                            atum_json_enabled = injector.DATAPACK_ENABLED_KEY in [str(v) for v in enabled]
        except Exception:
            atum_json_enabled = False
            atum_json_seed = ""

        recipe_book_path = effective_runtime.mods_dir / injector.RECIPE_BOOK_JAR_NAME
        dragon_patch_path = effective_runtime.mods_dir / injector.DRAGON_NODE_PATCH_JAR_NAME
        datapack_path = effective_runtime.atum_datapacks_dir / injector.DATAPACK_NAME

        active_token: MpkInjectionToken | None = getattr(app.state, "mpk_injection_token", None)
        atum_injected = bool(active_token is not None and active_token.atum_jar_injected)
        injected_components = [
            {
                "id": "atum_mod",
                "name": "Atum Mod",
                "kind": "mod",
                "description": "Altered version of Atum that allows set seed injection.",
                "expected_path": atum_mod_path,
                "present": bool(atum_mod_path),
                "active": atum_injected and not legal_ranked_instance,
                "enabled": not legal_ranked_instance,
            },
            {
                "id": "recipe_book_mod",
                "name": "Recipe Book Mod",
                "kind": "mod",
                "description": "Opens the recipe book in inventory on run start.",
                "expected_path": str(recipe_book_path),
                "present": recipe_book_path.exists(),
                "active": recipe_book_enabled and recipe_book_path.exists(),
                "enabled": recipe_book_enabled and not legal_ranked_instance,
            },
            {
                "id": "dragon_node_patch_mod",
                "name": "Dragon Node Patch Mod",
                "kind": "mod",
                "description": "Patches first dragon followPath height roll (0-15 instead of 0-20).",
                "expected_path": str(dragon_patch_path),
                "present": dragon_patch_path.exists(),
                "active": dragon_patch_enabled and dragon_patch_path.exists(),
                "enabled": dragon_patch_enabled and not legal_ranked_instance,
            },
            {
                "id": "zdash_tracker_datapack",
                "name": "zdash_tracker Datapack",
                "kind": "datapack",
                "description": "Writes dragon/run telemetry into storage for parser ingestion and mutes spammy advancements.",
                "expected_path": str(datapack_path),
                "present": datapack_path.exists(),
                "active": datapack_path.exists(),
                "enabled": True,
            },
            {
                "id": "atum_json_patch",
                "name": "Atum JSON Patch",
                "kind": "config",
                "description": "Overrides Atum settings to add the zdash_tracker datapack.",
                "expected_path": str(effective_runtime.atum_json_path),
                "present": effective_runtime.atum_json_path.exists(),
                "active": atum_json_enabled,
                "seed": atum_json_seed,
                "enabled": True,
            },
        ]

    return {
        "mpk_log_path": str(mpk_log_path) if mpk_log_path is not None else "",
        "mpk_saves_dir": str(mpk_saves_dir) if mpk_saves_dir is not None else "",
        "mpk_enabled": bool(getattr(app.state, "mpk_enabled", False)),
        "mpk_setup_required": bool(getattr(app.state, "mpk_setup_required", False)),
        "mpk_setup_error": str(getattr(app.state, "mpk_setup_error", "") or ""),
        "mpk_injected": bool(getattr(app.state, "mpk_injected", False)),
        "mpk_runtime_active": bool(runtime is not None),
        "mpk_instance_path": mpk_instance_path,
        "db_path": str(DB_PATH),
        "poll_seconds": POLL_SECONDS,
        "mpk_log_exists": bool(mpk_log_path is not None and mpk_log_path.exists()),
        "mpk_reader_identity": db.get_state(mpk_identity_key, ""),
        "mpk_reader_position": int(db.get_state(mpk_position_key, "0") or "0"),
        "mpk_last_heartbeat_utc": db.get_state(mpk_heartbeat_key, ""),
        "mpk_injected_components": injected_components,
        "mpk_inject_recipe_book": recipe_book_enabled,
        "mpk_inject_dragon_patch": dragon_patch_enabled,
        "mpk_legal_ranked_instance": legal_ranked_instance,
    }


def _resolve_stronghold_map_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve()
        base = STRONGHOLD_MAPS_DIR.resolve()
    except Exception:
        return None
    if not str(resolved).startswith(str(base)):
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def _stronghold_svg_url(attempt_id: int, svg_path: str | None) -> str | None:
    if _resolve_stronghold_map_path(svg_path) is None:
        return None
    return f"/api/stronghold/attempt/{attempt_id}/svg"


def _stronghold_json_url(attempt_id: int, json_path: str | None) -> str | None:
    if _resolve_stronghold_map_path(json_path) is None:
        return None
    return f"/api/stronghold/attempt/{attempt_id}/map-json"


def _derive_nav_seconds_from_visits(visits: Any) -> float:
    if not isinstance(visits, list) or not visits:
        return 0.0
    enters: list[int] = []
    exits: list[int] = []
    for visit in visits:
        if not isinstance(visit, dict):
            continue
        enter_gt = int(visit.get("enter_gt", 0) or 0)
        exit_gt = int(visit.get("exit_gt", 0) or 0)
        if enter_gt > 0:
            enters.append(enter_gt)
        if exit_gt > 0:
            exits.append(exit_gt)
    if not enters or not exits:
        return 0.0
    start_gt = min(enters)
    end_gt = max(exits)
    if end_gt <= start_gt:
        return 0.0
    return float(end_gt - start_gt) / 20.0


def _stronghold_result_meta(portal_entered: Any, spectator_detected: Any) -> dict[str, object]:
    portal = int(portal_entered or 0) == 1
    spectator = int(spectator_detected or 0) == 1
    success = portal and not spectator
    if success:
        return {
            "portal_success": True,
            "portal_status_label": "yes",
            "portal_status_class": "status-success",
            "portal_fail_reason": "",
        }
    if spectator:
        return {
            "portal_success": False,
            "portal_status_label": "no (spectator)",
            "portal_status_class": "status-spectator",
            "portal_fail_reason": "spectator",
        }
    return {
        "portal_success": False,
        "portal_status_label": "no",
        "portal_status_class": "status-fail",
        "portal_fail_reason": "",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(DB_PATH)
    mpk_enabled_runtime = bool(MPK_ENABLED and MpkAttemptTracker is not None)
    if MPK_ENABLED and MpkAttemptTracker is None:
        print(
            "[zero-dash] MPK tracker module missing (app/mpk_attempt_tracker.py)."
        )

    app.state.db = db
    app.state.mpk_watcher = None
    app.state.mpk_enabled = mpk_enabled_runtime
    app.state.mpk_runtime = None
    app.state.mpk_setup_required = False
    app.state.mpk_setup_error = ""
    app.state.mpk_injected = False
    app.state.mpk_injection_token = None
    app.state.mpk_injector = MpkInjector(Path(__file__).resolve().parents[1])
    app.state.mpk_lock = threading.RLock()
    app.state.started_at = utc_now()
    app.state.dashboard_cache = {}
    with app.state.mpk_lock:
        _init_mpk_runtime(app, db)
    try:
        yield
    finally:
        with app.state.mpk_lock:
            _stop_mpk_runtime(app, revert_injection=True)
        db.close()


app = FastAPI(title="Zero Cycle Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/stronghold")
def stronghold_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "stronghold.html")


@app.get("/stronghold/{attempt_id}")
def stronghold_detail_page(attempt_id: int) -> FileResponse:
    return FileResponse(STATIC_DIR / "stronghold_detail.html")


@app.get("/api/health")
def health(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    runtime_payload = _runtime_health_payload(request.app, db)
    return {
        "ok": True,
        "started_at": request.app.state.started_at,
        "now": utc_now(),
        **runtime_payload,
    }


@app.post("/api/setup/mpk-instance")
def set_mpk_instance(request: Request, payload: MpkSetupRequest) -> dict[str, object]:
    db: Database = request.app.state.db
    if not bool(getattr(request.app.state, "mpk_enabled", False)):
        return {"ok": False, "error": "MPK tracking is disabled."}
    injector: MpkInjector = request.app.state.mpk_injector
    normalized, err = injector.normalize_instance_path(payload.path)
    if normalized is None:
        return {"ok": False, "error": err or "Invalid instance path."}
    with request.app.state.mpk_lock:
        _stop_mpk_runtime(request.app, revert_injection=True)
        legal_ranked_instance = (
            _configured_legal_ranked_instance(db)
            if payload.legal_ranked_instance is None
            else bool(payload.legal_ranked_instance)
        )
        recipe_book_enabled = (
            _configured_recipe_book_enabled(db)
            if payload.open_recipe_book_on_run_start is None
            else bool(payload.open_recipe_book_on_run_start)
        )
        dragon_patch_enabled = (
            _configured_dragon_patch_enabled(db)
            if payload.enable_dragon_node_patch is None
            else bool(payload.enable_dragon_node_patch)
        )
        if legal_ranked_instance:
            recipe_book_enabled = False
            dragon_patch_enabled = False
        db.set_state("setup.mpk_instance_path", str(normalized))
        db.set_state("setup.inject_recipe_book", "1" if recipe_book_enabled else "0")
        db.set_state("setup.inject_dragon_patch", "1" if dragon_patch_enabled else "0")
        db.set_state("setup.legal_ranked_instance", "1" if legal_ranked_instance else "0")
        if legal_ranked_instance:
            set_mpk_full_random_override(db, True)
        _start_mpk_runtime(
            request.app,
            db,
            normalized,
            inject_recipe_book=recipe_book_enabled,
            inject_dragon_patch=dragon_patch_enabled,
            legal_ranked_instance=legal_ranked_instance,
        )
        request.app.state.dashboard_cache = {}
        status = _runtime_health_payload(request.app, db)
    if bool(status.get("mpk_setup_required", False)):
        return {
            "ok": False,
            "error": str(status.get("mpk_setup_error", "Failed to setup MPK runtime.")),
            "status": status,
        }
    return {"ok": True, "status": status}


@app.post("/api/setup/mpk-clear")
def clear_mpk_instance(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    if not bool(getattr(request.app.state, "mpk_enabled", False)):
        return {"ok": False, "error": "MPK tracking is disabled."}
    with request.app.state.mpk_lock:
        _stop_mpk_runtime(request.app, revert_injection=True)
        _clear_saved_mpk_path(db)
        request.app.state.mpk_setup_required = True
        request.app.state.mpk_setup_error = "No MPK instance path configured yet."
        request.app.state.dashboard_cache = {}
        status = _runtime_health_payload(request.app, db)
    return {"ok": True, "status": status}


def _normalize_filter(
    include_1_8: bool,
    rotation: str,
    window: str,
    tower: str | None,
    side: str | None,
    seed_mode: str,
    leniency_target: float | None,
    detail: str,
) -> tuple[bool, str, str, str | None, str | None, str, str, float | None, str]:
    rotation_norm = (rotation or "both").strip().lower()
    if rotation_norm not in {"both", "cw", "ccw"}:
        rotation_norm = "both"
    window_norm = (window or "all").strip().lower()
    if window_norm not in {"all", "current_session", "last_10", "last_25", "last_50", "last_100"}:
        window_norm = "all"
    detail_norm = (detail or "full").strip().lower()
    if detail_norm not in {"light", "full"}:
        detail_norm = "full"
    source_norm = "mpk"
    seed_mode_norm = (seed_mode or "all").strip().lower()
    if seed_mode_norm not in {"all", "full_random", "set_seed"}:
        seed_mode_norm = "all"
    leniency_norm: float | None
    if leniency_target is None:
        leniency_norm = None
    else:
        try:
            leniency_norm = float(leniency_target)
        except (TypeError, ValueError):
            leniency_norm = 0.0
        if leniency_norm != leniency_norm:  # NaN
            leniency_norm = 0.0
    tower_norm = None if tower in {None, "", "__GLOBAL__"} else str(tower)
    side_norm = None if side in {None, "", "__GLOBAL__"} else str(side)
    if side_norm not in {None, "Front", "Back"}:
        side_norm = None
    return (
        bool(include_1_8),
        rotation_norm,
        window_norm,
        tower_norm,
        side_norm,
        source_norm,
        seed_mode_norm,
        leniency_norm,
        detail_norm,
    )


def _build_dashboard_payload_cached(
    request: Request,
    *,
    include_1_8: bool,
    rotation: str,
    window: str,
    tower: str | None,
    side: str | None,
    seed_mode: str,
    leniency_target: float | None,
    detail: str,
) -> dict[str, Any]:
    db: Database = request.app.state.db
    cache: dict[Any, dict[str, Any]] = request.app.state.dashboard_cache
    include_1_8, rotation, window, tower, side, attempt_source, seed_mode, leniency_target, detail = _normalize_filter(
        include_1_8, rotation, window, tower, side, seed_mode, leniency_target, detail
    )
    if leniency_target is None:
        try:
            leniency_target = float(db.get_state("mpk.practice.leniency_target", "0") or "0")
        except ValueError:
            leniency_target = 0.0
    data_version_row = db.query_one("PRAGMA data_version")
    data_version = int(data_version_row[0]) if data_version_row is not None else 0
    max_attempt_row = db.query_one("SELECT COALESCE(MAX(id), 0) AS max_id FROM attempts")
    max_attempt_id = int(max_attempt_row["max_id"]) if max_attempt_row is not None else 0
    cache_key = (
        detail,
        include_1_8,
        rotation,
        window,
        tower,
        side,
        attempt_source,
        seed_mode,
        round(leniency_target, 4),
        data_version,
        max_attempt_id,
    )
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
            attempt_source=attempt_source,
            attempt_seed_mode=seed_mode,
            leniency_target=leniency_target,
            detail=detail,
        )
        ttl = 0.4 if detail == "light" else 1.2
        cache[cache_key] = {"payload": payload, "expires_at": now + ttl}
        if len(cache) > 128:
            stale_keys = [k for k, v in cache.items() if float(v.get("expires_at", 0.0)) <= now]
            for k in stale_keys[:64]:
                cache.pop(k, None)
    payload["server_time_utc"] = utc_now()
    payload["db_path"] = str(DB_PATH)
    payload["latest_attempt_id"] = max_attempt_id
    return payload


@app.get("/api/dashboard")
def dashboard(
    request: Request,
    include_1_8: bool = Query(default=False),
    rotation: str = Query(default="both"),
    window: str = Query(default="all"),
    tower: str | None = Query(default=None),
    side: str | None = Query(default=None),
    seed_mode: str = Query(default="all"),
    leniency_target: float | None = Query(default=None),
    detail: str = Query(default="full"),
) -> dict[str, object]:
    return _build_dashboard_payload_cached(
        request,
        include_1_8=include_1_8,
        rotation=rotation,
        window=window,
        tower=tower,
        side=side,
        seed_mode=seed_mode,
        leniency_target=leniency_target,
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
    seed_mode: str = Query(default="all"),
    leniency_target: float | None = Query(default=None),
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
                seed_mode=seed_mode,
                leniency_target=leniency_target,
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
    tok = ATTEMPT_SOURCE_CTX.set("mpk")
    try:
        return {"attempts": compute_recent_attempts(db, limit=limit)}
    finally:
        ATTEMPT_SOURCE_CTX.reset(tok)


@app.get("/api/stronghold/recent")
def recent_stronghold_runs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, object]:
    payload = stronghold_dashboard(request, limit=limit)
    return {"runs": payload.get("runs", [])}


@app.get("/api/stronghold/version")
def stronghold_version(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    row = db.query_one(
        """
        SELECT
            COALESCE(MAX(id), 0) AS latest_attempt_id,
            COALESCE(
                MAX(
                    CASE
                        WHEN COALESCE(stronghold_map_svg_path, '') <> '' THEN id
                        ELSE 0
                    END
                ),
                0
            ) AS latest_mapped_attempt_id
        FROM attempts
        WHERE COALESCE(attempt_source, 'practice') = 'mpk'
          AND COALESCE(stronghold_sample_count, 0) > 0
        """
    )
    latest_attempt_id = int(row["latest_attempt_id"]) if row is not None else 0
    latest_mapped_attempt_id = int(row["latest_mapped_attempt_id"]) if row is not None else 0
    return {
        "latest_attempt_id": latest_attempt_id,
        "latest_mapped_attempt_id": latest_mapped_attempt_id,
    }


@app.get("/api/stronghold/dashboard")
def stronghold_dashboard(
    request: Request,
    limit: int = Query(default=40, ge=1, le=300),
) -> dict[str, object]:
    db: Database = request.app.state.db
    summary_row = db.query_one(
        """
        SELECT
            COUNT(*) AS attempts,
            SUM(
                CASE
                    WHEN COALESCE(stronghold_portal_room_entered, 0) = 1
                     AND COALESCE(stronghold_spectator_detected, 0) = 0
                    THEN 1
                    ELSE 0
                END
            ) AS portal_successes,
            AVG(stronghold_starter_seconds) AS avg_starter_seconds,
            AVG(stronghold_avg_room_seconds) AS avg_room_seconds,
            AVG(
                CASE
                    WHEN COALESCE(stronghold_nav_seconds, 0) > 0 THEN stronghold_nav_seconds
                    WHEN COALESCE(stronghold_nav_ticks, 0) > 0 THEN (stronghold_nav_ticks / 20.0)
                    ELSE NULL
                END
            ) AS avg_nav_seconds,
            AVG(stronghold_rooms_entered) AS avg_rooms_entered,
            AVG(stronghold_room_delta) AS avg_room_delta,
            AVG(stronghold_optimal_rooms) AS avg_optimal_rooms
        FROM attempts
        WHERE COALESCE(attempt_source, 'practice') = 'mpk'
          AND COALESCE(stronghold_sample_count, 0) > 0
        """
    )
    total_attempts = int(summary_row["attempts"]) if summary_row is not None and summary_row["attempts"] is not None else 0
    portal_successes = (
        int(summary_row["portal_successes"])
        if summary_row is not None and summary_row["portal_successes"] is not None
        else 0
    )
    summary = {
        "attempts": total_attempts,
        "portal_successes": portal_successes,
        "portal_success_rate": round((portal_successes / total_attempts) * 100.0, 2)
        if total_attempts > 0
        else 0.0,
        "avg_starter_seconds": float(summary_row["avg_starter_seconds"] or 0.0) if summary_row is not None else 0.0,
        "avg_room_seconds": float(summary_row["avg_room_seconds"] or 0.0) if summary_row is not None else 0.0,
        "avg_nav_seconds": float(summary_row["avg_nav_seconds"] or 0.0) if summary_row is not None else 0.0,
        "avg_rooms_entered": float(summary_row["avg_rooms_entered"] or 0.0) if summary_row is not None else 0.0,
        "avg_room_delta": float(summary_row["avg_room_delta"] or 0.0) if summary_row is not None else 0.0,
        "avg_optimal_rooms": float(summary_row["avg_optimal_rooms"] or 0.0) if summary_row is not None else 0.0,
    }

    rows = db.query_all(
        """
        SELECT
            id,
            started_at_utc,
            ended_at_utc,
            status,
            fail_reason,
            tower_name,
            zero_type,
            o_level,
            standing_height,
            world_name,
            world_seed,
            stronghold_eye_spy_gt,
            stronghold_end_enter_gt,
            stronghold_spectator_detected,
            stronghold_spectator_gt,
            stronghold_nav_ticks,
            stronghold_nav_seconds,
            stronghold_sample_count,
            stronghold_rooms_entered,
            stronghold_starter_ticks,
            stronghold_starter_seconds,
            stronghold_avg_room_ticks,
            stronghold_avg_room_seconds,
            stronghold_portal_room_entered,
            stronghold_optimal_rooms,
            stronghold_optimal_edges,
            stronghold_room_delta,
            stronghold_map_json_path,
            stronghold_map_svg_path
        FROM attempts
        WHERE COALESCE(attempt_source, 'practice') = 'mpk'
          AND COALESCE(stronghold_sample_count, 0) > 0
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    runs: list[dict[str, object]] = []
    for row in rows:
        attempt_id = int(row["id"])
        zero_type = str(row["zero_type"] or "")
        side = "Front" if zero_type.startswith("Front ") else "Back" if zero_type.startswith("Back ") else "Unknown"
        result_meta = _stronghold_result_meta(
            row["stronghold_portal_room_entered"],
            row["stronghold_spectator_detected"],
        )
        svg_url = _stronghold_svg_url(attempt_id, row["stronghold_map_svg_path"])
        json_url = _stronghold_json_url(attempt_id, row["stronghold_map_json_path"])
        nav_seconds = float(row["stronghold_nav_seconds"] or 0.0)
        nav_ticks = int(row["stronghold_nav_ticks"] or 0)
        if nav_seconds <= 0.0 and json_url is not None:
            json_path = _resolve_stronghold_map_path(row["stronghold_map_json_path"])
            if json_path is not None:
                try:
                    parsed = json.loads(json_path.read_text(encoding="utf-8"))
                    nav_seconds_fallback = _derive_nav_seconds_from_visits(parsed.get("visits", []))
                    if nav_seconds_fallback > 0.0:
                        nav_seconds = nav_seconds_fallback
                        nav_ticks = int(round(nav_seconds_fallback * 20.0))
                except Exception:
                    pass
        runs.append(
            {
                **dict(row),
                "stronghold_nav_ticks": nav_ticks if nav_ticks > 0 else None,
                "stronghold_nav_seconds": nav_seconds if nav_seconds > 0 else None,
                "side": side,
                "portal_success": bool(result_meta["portal_success"]),
                "portal_status_label": str(result_meta["portal_status_label"]),
                "portal_status_class": str(result_meta["portal_status_class"]),
                "portal_fail_reason": str(result_meta["portal_fail_reason"]),
                "detail_url": f"/stronghold/{attempt_id}",
                "svg_url": svg_url,
                "json_url": json_url,
                "has_map": bool(svg_url),
            }
        )
    return {"summary": summary, "runs": runs}


@app.get("/api/stronghold/attempt/{attempt_id}")
def stronghold_attempt_detail(request: Request, attempt_id: int) -> dict[str, object]:
    db: Database = request.app.state.db
    row = db.query_one(
        """
        SELECT
            id,
            started_at_utc,
            ended_at_utc,
            status,
            fail_reason,
            tower_name,
            zero_type,
            o_level,
            standing_height,
            world_name,
            world_seed,
            stronghold_eye_spy_gt,
            stronghold_end_enter_gt,
            stronghold_spectator_detected,
            stronghold_spectator_gt,
            stronghold_nav_ticks,
            stronghold_nav_seconds,
            stronghold_sample_count,
            stronghold_rooms_entered,
            stronghold_starter_ticks,
            stronghold_starter_seconds,
            stronghold_avg_room_ticks,
            stronghold_avg_room_seconds,
            stronghold_portal_room_entered,
            stronghold_optimal_rooms,
            stronghold_optimal_edges,
            stronghold_room_delta,
            stronghold_map_json_path,
            stronghold_map_svg_path
        FROM attempts
        WHERE id = ?
          AND COALESCE(attempt_source, 'practice') = 'mpk'
        """,
        (attempt_id,),
    )
    if row is None:
        return {"ok": False, "error": "Attempt not found."}
    samples = db.query_all(
        """
        SELECT
            sample_index,
            gt,
            x,
            y,
            z,
            dim
        FROM stronghold_samples
        WHERE attempt_id = ?
        ORDER BY sample_index ASC
        """,
        (attempt_id,),
    )
    attempt = dict(row)
    zero_type = str(attempt.get("zero_type") or "")
    attempt["side"] = (
        "Front" if zero_type.startswith("Front ") else "Back" if zero_type.startswith("Back ") else "Unknown"
    )
    result_meta = _stronghold_result_meta(
        attempt.get("stronghold_portal_room_entered"),
        attempt.get("stronghold_spectator_detected"),
    )
    attempt["portal_success"] = bool(result_meta["portal_success"])
    attempt["portal_status_label"] = str(result_meta["portal_status_label"])
    attempt["portal_status_class"] = str(result_meta["portal_status_class"])
    attempt["portal_fail_reason"] = str(result_meta["portal_fail_reason"])
    attempt["detail_url"] = f"/stronghold/{attempt_id}"
    attempt["svg_url"] = _stronghold_svg_url(attempt_id, attempt.get("stronghold_map_svg_path"))
    attempt["json_url"] = _stronghold_json_url(attempt_id, attempt.get("stronghold_map_json_path"))
    map_payload: dict[str, object] | None = None
    json_path = _resolve_stronghold_map_path(attempt.get("stronghold_map_json_path"))
    if json_path is not None:
        try:
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                map_payload = parsed
        except Exception:
            map_payload = None
    analysis: dict[str, object] | None = None
    if map_payload is not None:
        pieces = map_payload.get("pieces", [])
        visits = map_payload.get("visits", [])
        doors = map_payload.get("doors", [])
        chests = map_payload.get("chests", [])
        spawners = map_payload.get("spawners", [])
        nav_seconds_fallback = _derive_nav_seconds_from_visits(visits)
        if float(attempt.get("stronghold_nav_seconds") or 0.0) <= 0.0 and nav_seconds_fallback > 0.0:
            attempt["stronghold_nav_seconds"] = nav_seconds_fallback
            attempt["stronghold_nav_ticks"] = int(round(nav_seconds_fallback * 20.0))
        analysis = {
            "sample_stats": map_payload.get("sample_stats", {}),
            "starter": map_payload.get("starter", {}),
            "optimal_nav": map_payload.get("optimal_nav", {}),
            "visits": visits if isinstance(visits, list) else [],
            "pieces_count": len(pieces) if isinstance(pieces, list) else 0,
            "doors_count": len(doors) if isinstance(doors, list) else 0,
            "chests_count": len(chests) if isinstance(chests, list) else 0,
            "spawners_count": len(spawners) if isinstance(spawners, list) else 0,
        }
    return {
        "ok": True,
        "attempt": attempt,
        "samples": [dict(s) for s in samples],
        "analysis": analysis,
    }


@app.get("/api/stronghold/attempt/{attempt_id}/svg")
def stronghold_attempt_svg(request: Request, attempt_id: int):
    db: Database = request.app.state.db
    row = db.query_one(
        """
        SELECT stronghold_map_svg_path
        FROM attempts
        WHERE id = ?
          AND COALESCE(attempt_source, 'practice') = 'mpk'
        """,
        (attempt_id,),
    )
    if row is None:
        return {"ok": False, "error": "Attempt not found."}
    svg_path = _resolve_stronghold_map_path(row["stronghold_map_svg_path"])
    if svg_path is None:
        return {"ok": False, "error": "SVG not available."}
    return FileResponse(svg_path, media_type="image/svg+xml")


@app.get("/api/stronghold/attempt/{attempt_id}/map-json")
def stronghold_attempt_map_json(request: Request, attempt_id: int):
    db: Database = request.app.state.db
    row = db.query_one(
        """
        SELECT stronghold_map_json_path
        FROM attempts
        WHERE id = ?
          AND COALESCE(attempt_source, 'practice') = 'mpk'
        """,
        (attempt_id,),
    )
    if row is None:
        return {"ok": False, "error": "Attempt not found."}
    json_path = _resolve_stronghold_map_path(row["stronghold_map_json_path"])
    if json_path is None:
        return {"ok": False, "error": "JSON not available."}
    return FileResponse(json_path, media_type="application/json")


@app.get("/api/mpk/lock-targets")
def get_mpk_lock_targets(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    keys = get_mpk_locked_targets(db)
    return {"ok": True, "locked_target_keys": keys}


@app.post("/api/mpk/lock-target")
def set_mpk_lock_target(
    request: Request,
    target_key: str = Query(...),
    locked: bool | None = Query(default=None),
) -> dict[str, object]:
    db: Database = request.app.state.db
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return {"ok": False, "error": "Invalid MPK target key.", "locked_target_keys": get_mpk_locked_targets(db)}
    normalized_key = f"mpk|{parsed[0]}|{parsed[1]}|{parsed[2]}"
    keys = toggle_mpk_locked_target(db, normalized_key, locked=locked)
    return {"ok": True, "locked_target_keys": keys}


@app.post("/api/mpk/lock-targets/clear")
def clear_mpk_lock_targets(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    keys = set_mpk_locked_targets(db, [])
    return {"ok": True, "locked_target_keys": keys}


@app.post("/api/mpk/lock-targets/set-single")
def set_single_mpk_lock_target(
    request: Request,
    target_key: str = Query(...),
) -> dict[str, object]:
    db: Database = request.app.state.db
    parsed = parse_mpk_target_key(target_key)
    if parsed is None:
        return {"ok": False, "error": "Invalid MPK target key.", "locked_target_keys": get_mpk_locked_targets(db)}
    normalized_key = f"mpk|{parsed[0]}|{parsed[1]}|{parsed[2]}"
    keys = set_mpk_locked_targets(db, [normalized_key])
    request.app.state.dashboard_cache = {}
    return {"ok": True, "locked_target_keys": keys}


@app.post("/api/mpk/leniency-target")
def set_mpk_leniency_target(
    request: Request,
    value: float = Query(...),
) -> dict[str, object]:
    db: Database = request.app.state.db
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = 0.0
    if not math.isfinite(normalized):
        normalized = 0.0
    db.set_state("mpk.practice.leniency_target", str(normalized))
    request.app.state.dashboard_cache = {}
    return {"ok": True, "leniency_target": normalized}


@app.post("/api/mpk/full-random-override")
def set_mpk_full_random_override_route(
    request: Request,
    enabled: bool | None = Query(default=None),
) -> dict[str, object]:
    db: Database = request.app.state.db
    if not bool(getattr(request.app.state, "mpk_enabled", False)):
        return {"ok": False, "error": "MPK tracking is disabled."}
    with request.app.state.mpk_lock:
        legal_ranked_instance = _configured_legal_ranked_instance(db)
        current = is_mpk_full_random_override_enabled(db)
        target = (not current) if enabled is None else bool(enabled)
        if legal_ranked_instance and not target:
            return {"ok": False, "error": "Full random is forced while Legal Ranked Instance mode is enabled."}
        result = set_mpk_full_random_override(db, target)
        if target:
            # Re-apply seed clear against current runtime path immediately.
            clear_state = clear_runtime_atum_seed(db)
            if clear_state.get("seed_clear_error"):
                result["seed_clear_error"] = str(clear_state.get("seed_clear_error"))
            result["atum_json_path"] = str(clear_state.get("atum_json_path", result.get("atum_json_path", "")))
            result["seed_cleared"] = bool(clear_state.get("seed_cleared", result.get("seed_cleared", False)))
        request.app.state.dashboard_cache = {}
    return {"ok": True, **result}


@app.post("/api/mpk/weak-lock/skip")
def skip_mpk_weak_lock_route(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    if not bool(getattr(request.app.state, "mpk_enabled", False)):
        return {"ok": False, "error": "MPK tracking is disabled."}
    with request.app.state.mpk_lock:
        result = skip_current_mpk_weak_lock(db)
        request.app.state.dashboard_cache = {}
    return {"ok": True, **result}


@app.post("/api/mpk/weak-lock/replace-with-open")
def replace_mpk_weak_lock_with_open_route(request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    if not bool(getattr(request.app.state, "mpk_enabled", False)):
        return {"ok": False, "error": "MPK tracking is disabled."}
    with request.app.state.mpk_lock:
        result = replace_current_mpk_weak_lock_with_open(db)
        request.app.state.dashboard_cache = {}
    if not bool(result.get("ok", False)):
        return {"ok": False, **result}
    return {"ok": True, **result}


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
