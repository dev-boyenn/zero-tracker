from __future__ import annotations

from contextvars import ContextVar
import re
from typing import Any

from .database import Database

WINDOW_MIN_ID_CTX: ContextVar[int | None] = ContextVar("window_min_id", default=None)
WINDOW_START_UTC_CTX: ContextVar[str | None] = ContextVar("window_start_utc", default=None)
INCLUDE_STRAIGHT_CTX: ContextVar[bool] = ContextVar("include_straight", default=True)
ROTATION_FILTER_CTX: ContextVar[str] = ContextVar("rotation_filter", default="both")
ATTEMPT_SOURCE_CTX: ContextVar[str] = ContextVar("attempt_source_filter", default="all")


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _scope_where(
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
    include_where: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if zero_type is not None:
        clauses.append("COALESCE(zero_type, 'Unknown') = ?")
        params.append(zero_type)
    if tower_name is not None:
        clauses.append("COALESCE(tower_name, 'Unknown') = ?")
        params.append(tower_name)
    if front_back is not None:
        if front_back == "Front":
            clauses.append("COALESCE(zero_type, '') LIKE 'Front %'")
        elif front_back == "Back":
            clauses.append("COALESCE(zero_type, '') LIKE 'Back %'")
        else:
            clauses.append(
                "COALESCE(zero_type, '') NOT LIKE 'Front %' AND COALESCE(zero_type, '') NOT LIKE 'Back %'"
            )
    if not INCLUDE_STRAIGHT_CTX.get():
        clauses.append("COALESCE(zero_type, '') NOT LIKE '%Straight%'")
    rotation_filter = ROTATION_FILTER_CTX.get()
    if rotation_filter == "cw":
        clauses.append(
            "UPPER(COALESCE(zero_type, '')) LIKE '%CW' AND UPPER(COALESCE(zero_type, '')) NOT LIKE '%CCW'"
        )
    elif rotation_filter == "ccw":
        clauses.append("UPPER(COALESCE(zero_type, '')) LIKE '%CCW'")
    attempt_source = ATTEMPT_SOURCE_CTX.get()
    if attempt_source in {"practice", "mpk"}:
        clauses.append("COALESCE(attempt_source, 'practice') = ?")
        params.append(attempt_source)

    window_min_id = WINDOW_MIN_ID_CTX.get()
    if window_min_id is not None:
        clauses.append("id >= ?")
        params.append(window_min_id)
    window_start_utc = WINDOW_START_UTC_CTX.get()
    if window_start_utc is not None:
        clauses.append("started_at_utc >= ?")
        params.append(window_start_utc)

    if not clauses:
        return ("", [])
    prefix = "WHERE" if include_where else "AND"
    return (f" {prefix} " + " AND ".join(clauses), params)


def _type_where(zero_type: str | None, include_where: bool = True) -> tuple[str, list[Any]]:
    return _scope_where(zero_type=zero_type, include_where=include_where)


def _compute_current_session_start_utc(db: Database) -> str | None:
    where, params = _scope_where(include_where=False)
    row = db.query_one(
        f"""
        WITH finished AS (
            SELECT id, started_at_utc
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND started_at_utc IS NOT NULL{where}
            ORDER BY started_at_utc ASC, id ASC
        ),
        marks AS (
            SELECT
                id,
                started_at_utc,
                CASE
                    WHEN LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) IS NULL THEN 1
                    WHEN (
                        julianday(started_at_utc)
                        - julianday(LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC))
                    ) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM finished
        ),
        numbered AS (
            SELECT
                started_at_utc,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM marks
        )
        SELECT MIN(started_at_utc) AS session_start_utc
        FROM numbered
        WHERE session_index = (SELECT MAX(session_index) FROM numbered)
        """,
        params,
    )
    if row is None:
        return None
    value = row["session_start_utc"]
    return str(value) if value is not None else None


def _compute_current_session_start_utc_unscoped(db: Database) -> str | None:
    row = db.query_one(
        """
        WITH finished AS (
            SELECT id, started_at_utc
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND started_at_utc IS NOT NULL
            ORDER BY started_at_utc ASC, id ASC
        ),
        marks AS (
            SELECT
                id,
                started_at_utc,
                CASE
                    WHEN LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) IS NULL THEN 1
                    WHEN (
                        julianday(started_at_utc)
                        - julianday(LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC))
                    ) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM finished
        ),
        numbered AS (
            SELECT
                started_at_utc,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM marks
        )
        SELECT MIN(started_at_utc) AS session_start_utc
        FROM numbered
        WHERE session_index = (SELECT MAX(session_index) FROM numbered)
        """
    )
    if row is None:
        return None
    value = row["session_start_utc"]
    return str(value) if value is not None else None


def _compute_window_bounds(db: Database) -> dict[str, dict[str, Any]]:
    bounds: dict[str, dict[str, Any]] = {
        "all": {"min_id": None, "start_utc": None},
        "current_session": {"min_id": None, "start_utc": _compute_current_session_start_utc(db)},
    }
    for n in (10, 25, 50, 100):
        where, params = _scope_where(include_where=False)
        row = db.query_one(
            f"""
            SELECT MIN(id) AS min_id
            FROM (
                SELECT id
                FROM attempts
                WHERE status IN ('success', 'fail'){where}
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (*params, n),
        )
        min_id = int(row["min_id"]) if row is not None and row["min_id"] is not None else None
        bounds[f"last_{n}"] = {"min_id": min_id, "start_utc": None}
    return bounds


def compute_practice_next_widget(db: Database) -> dict[str, Any]:
    if ATTEMPT_SOURCE_CTX.get() != "practice":
        return {
            "disabled": True,
            "disabled_reason": "Practice recommendations are only available for practice-map data.",
            "recommended": None,
            "window_size": 250,
        }

    lock_key = "practice_next.lock_target"
    lock_anchor_key = "practice_next.lock_anchor_attempt_id"
    min_streak_to_swap = 3
    distribution_threshold_pct = 80.0
    min_points_per_target = 2
    expected_towers = [
        "Small Boy",
        "Small Cage",
        "Tall Cage",
        "M-85",
        "M-88",
        "M-91",
        "T-94",
        "T-97",
        "T-100",
        "Tall Boy",
    ]
    expected_sides = ("Front", "Back")
    total_possible_targets = (len(expected_towers) * len(expected_sides)) + len(expected_sides)

    def _target_key(target: dict[str, Any]) -> str:
        return f"{target['target_kind']}|{target['tower_name']}|{target['side']}|{target['rotation']}"

    def _slug_tower(name: str) -> str:
        slug = []
        prev_underscore = False
        for ch in name.lower():
            if ch.isalnum():
                slug.append(ch)
                prev_underscore = False
            else:
                if not prev_underscore:
                    slug.append("_")
                    prev_underscore = True
        result = "".join(slug).strip("_")
        return result or "tower"

    def _normalize_tower(name: str) -> str:
        cleaned = re.sub(r"\s*\(\d+\)\s*$", "", str(name or "").strip())
        return cleaned

    def _target_matches_from_key(target_key: str) -> tuple[str, str, str, str]:
        parts = target_key.split("|", 3)
        if len(parts) != 4:
            return ("", "", "", "")
        return (parts[0], parts[1], parts[2], parts[3])

    def _success_streak_for_target(target_key: str, min_id: int, anchor_after_id: int = 0) -> int:
        target_kind, tower_name, side, rotation = _target_matches_from_key(target_key)
        if target_kind not in {"tower", "one_eight"}:
            return 0
        lower_bound_id = max(min_id, anchor_after_id + 1)
        params: list[Any] = [lower_bound_id]
        target_where = """
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END = ?
            AND
            CASE
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CCW' THEN 'CCW'
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CW' THEN 'CW'
                ELSE 'Unknown'
            END = ?
        """
        params.extend([side, rotation])
        if target_kind == "tower":
            target_where += " AND COALESCE(zero_type, '') NOT LIKE '%Straight%' AND COALESCE(tower_name, 'Unknown') = ?"
            params.append(tower_name)
        else:
            target_where += " AND COALESCE(zero_type, '') LIKE '%Straight%'"
        rows = db.query_all(
            f"""
            SELECT status
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND id >= ?
              AND {target_where}
            ORDER BY id DESC
            LIMIT 200
            """,
            params,
        )
        streak = 0
        for row in rows:
            if row["status"] == "success":
                streak += 1
            else:
                break
        return streak

    row = db.query_one(
        """
        SELECT MIN(id) AS min_id
        FROM (
            SELECT id
            FROM attempts
            WHERE status IN ('success', 'fail')
            ORDER BY id DESC
            LIMIT 250
        )
        """
    )
    min_id = int(row["min_id"]) if row is not None and row["min_id"] is not None else None
    if min_id is None:
        recommended = {
            "target_kind": "full_random",
            "label": "Full Random",
            "tower_name": "Full Random",
            "side": "Random",
            "rotation": "Random",
            "attempts": 0,
            "successes": 0,
            "success_rate": 0.0,
            "chat_command": "/function practice:zdash/set/full_random",
        }
        return {
            "min_id": None,
            "window_size": 250,
            "recommended": recommended,
            "lock_applied": False,
            "current_streak_on_recommended": 0,
            "min_streak_to_swap": min_streak_to_swap,
            "distribution": {
                "coverage_percent": 0.0,
                "threshold_percent": distribution_threshold_pct,
                "min_points_per_target": min_points_per_target,
                "qualified_targets": 0,
                "total_targets": total_possible_targets,
                "is_sufficient": False,
            },
            "missing_towers": [],
            "missing_1_8_groups": [],
        }

    common_cte = """
        WITH cur AS (
            SELECT
                id,
                COALESCE(tower_name, 'Unknown') AS tower_name,
                COALESCE(zero_type, 'Unknown') AS zero_type,
                CASE
                    WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                    WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                    ELSE 'Unknown'
                END AS side,
                CASE
                    WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CCW' THEN 'CCW'
                    WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CW' THEN 'CW'
                    ELSE 'Unknown'
                END AS rotation,
                CASE
                    WHEN COALESCE(zero_type, '') LIKE '%Straight%' THEN 1
                    ELSE 0
                END AS is_one_eight,
                status
            FROM attempts
            WHERE status IN ('success', 'fail')
              AND id >= ?
        )
    """
    non_straight_rows = db.query_all(
        common_cte
        + """
        SELECT
            tower_name,
            side,
            rotation,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM cur
        WHERE is_one_eight = 0 AND tower_name <> 'Unknown'
        GROUP BY tower_name, side, rotation
        """,
        (min_id,),
    )
    straight_rows = db.query_all(
        common_cte
        + """
        SELECT
            side,
            rotation,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM cur
        WHERE is_one_eight = 1
        GROUP BY side, rotation
        """,
        (min_id,),
    )

    # Coverage gate uses broader buckets (tower+side, plus 1/8 by side)
    # so the threshold reflects practical sample breadth without splitting by CW/CCW.
    expected_target_keys: list[tuple[str, str]] = []
    for tower in expected_towers:
        for side in expected_sides:
            expected_target_keys.append((tower, side))
    for side in expected_sides:
        expected_target_keys.append(("1/8", side))

    attempt_counts_by_target: dict[tuple[str, str], int] = {
        key: 0 for key in expected_target_keys
    }

    candidates: list[dict[str, Any]] = []
    for row in non_straight_rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        tower_name = _normalize_tower(str(row["tower_name"]))
        key = (tower_name, str(row["side"]))
        if key in attempt_counts_by_target:
            attempt_counts_by_target[key] += attempts
        candidates.append(
            {
                "target_kind": "tower",
                "label": f"{tower_name} | {row['side']} | {row['rotation']}",
                "tower_name": tower_name,
                "side": row["side"],
                "rotation": row["rotation"],
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
            }
        )
    for row in straight_rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        key = ("1/8", str(row["side"]))
        if key in attempt_counts_by_target:
            attempt_counts_by_target[key] += attempts
        candidates.append(
            {
                "target_kind": "one_eight",
                "label": f"1/8 | {row['side']} | {row['rotation']}",
                "tower_name": "1/8",
                "side": row["side"],
                "rotation": row["rotation"],
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
            }
        )

    total_targets = len(expected_target_keys)
    qualified_targets = sum(
        1 for key in expected_target_keys if attempt_counts_by_target.get(key, 0) >= min_points_per_target
    )
    coverage_percent = round(_pct(qualified_targets, total_targets), 2)
    distribution_is_sufficient = coverage_percent >= distribution_threshold_pct

    recommended = None
    lock_applied = False
    current_streak = 0
    max_finished_id_row = db.query_one(
        """
        SELECT COALESCE(MAX(id), 0) AS max_id
        FROM attempts
        WHERE status IN ('success', 'fail')
        """
    )
    max_finished_id = int(max_finished_id_row["max_id"]) if max_finished_id_row is not None else 0

    def _set_lock_state(target: dict[str, Any]) -> None:
        db.set_state(lock_key, _target_key(target))
        db.set_state(lock_anchor_key, str(max_finished_id))

    def _clear_lock_state() -> None:
        db.set_state(lock_key, "")
        db.set_state(lock_anchor_key, "0")

    if not distribution_is_sufficient:
        recommended = {
            "target_kind": "full_random",
            "label": "Full Random",
            "tower_name": "Full Random",
            "side": "Random",
            "rotation": "Random",
            "attempts": 0,
            "successes": 0,
            "success_rate": 0.0,
            "chat_command": "/function practice:zdash/set/full_random",
        }
        _clear_lock_state()
    elif candidates:
        candidates.sort(key=lambda r: (r["success_rate"], -r["attempts"], r["label"]))
        by_key = {_target_key(c): c for c in candidates}
        proposed = candidates[0]
        locked_key = db.get_state(lock_key, "")
        try:
            lock_anchor_id = int(db.get_state(lock_anchor_key, "0") or "0")
        except ValueError:
            lock_anchor_id = 0
        if locked_key and locked_key in by_key:
            current_streak = _success_streak_for_target(
                locked_key, min_id=min_id, anchor_after_id=lock_anchor_id
            )
            if current_streak < min_streak_to_swap:
                recommended = by_key[locked_key]
                lock_applied = True
            else:
                # Force a target swap once lock goal is reached, even if locked target
                # would still rank as the worst by raw success rate.
                alternative = next((c for c in candidates if _target_key(c) != locked_key), None)
                recommended = alternative if alternative is not None else proposed
                _set_lock_state(recommended)
                current_streak = _success_streak_for_target(
                    _target_key(recommended), min_id=min_id, anchor_after_id=max_finished_id
                )
        else:
            recommended = proposed
            _set_lock_state(proposed)
            current_streak = _success_streak_for_target(
                _target_key(proposed), min_id=min_id, anchor_after_id=max_finished_id
            )
    if recommended is not None:
        side = str(recommended["side"]).lower()
        rotation = str(recommended["rotation"]).lower()
        if recommended["target_kind"] == "tower":
            tower_slug = _slug_tower(str(recommended["tower_name"]))
            recommended["chat_command"] = f"/function practice:zdash/set/{tower_slug}_{side}_{rotation}"
        elif recommended["target_kind"] == "one_eight":
            recommended["chat_command"] = f"/function practice:zdash/set/one_eight_{side}_{rotation}"

    all_towers_rows = db.query_all(
        """
        SELECT DISTINCT COALESCE(tower_name, 'Unknown') AS tower_name
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(zero_type, '') NOT LIKE '%Straight%'
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'
        ORDER BY tower_name ASC
        """
    )
    cur_towers_rows = db.query_all(
        """
        SELECT DISTINCT COALESCE(tower_name, 'Unknown') AS tower_name
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND id >= ?
          AND COALESCE(zero_type, '') NOT LIKE '%Straight%'
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'
        ORDER BY tower_name ASC
        """,
        (min_id,),
    )
    all_towers = {str(r["tower_name"]) for r in all_towers_rows}
    cur_towers = {str(r["tower_name"]) for r in cur_towers_rows}
    missing_towers = sorted(all_towers - cur_towers)

    all_18_rows = db.query_all(
        """
        SELECT DISTINCT
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            CASE
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CCW' THEN 'CCW'
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CW' THEN 'CW'
                ELSE 'Unknown'
            END AS rotation
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(zero_type, '') LIKE '%Straight%'
        """
    )
    cur_18_rows = db.query_all(
        """
        SELECT DISTINCT
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            CASE
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CCW' THEN 'CCW'
                WHEN UPPER(COALESCE(zero_type, '')) LIKE '%CW' THEN 'CW'
                ELSE 'Unknown'
            END AS rotation
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND id >= ?
          AND COALESCE(zero_type, '') LIKE '%Straight%'
        """,
        (min_id,),
    )
    all_18 = {f"{r['side']} {r['rotation']}" for r in all_18_rows}
    cur_18 = {f"{r['side']} {r['rotation']}" for r in cur_18_rows}
    missing_1_8_groups = sorted(all_18 - cur_18)

    return {
        "min_id": min_id,
        "window_size": 250,
        "recommended": recommended,
        "lock_applied": lock_applied,
        "current_streak_on_recommended": current_streak,
        "min_streak_to_swap": min_streak_to_swap,
        "distribution": {
            "coverage_percent": coverage_percent,
            "threshold_percent": distribution_threshold_pct,
            "min_points_per_target": min_points_per_target,
            "qualified_targets": qualified_targets,
            "total_targets": total_targets,
            "is_sufficient": distribution_is_sufficient,
        },
        "missing_towers": missing_towers,
        "missing_1_8_groups": missing_1_8_groups,
    }


def compute_type_overview(db: Database) -> list[dict[str, Any]]:
    rows = db.query_all(
        """
        SELECT
            COALESCE(zero_type, 'Unknown') AS zero_type,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN status IN ('success','fail') AND bed_count > 0 THEN 1 ELSE 0 END) AS setup_reached,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        GROUP BY COALESCE(zero_type, 'Unknown')
        ORDER BY attempts DESC, zero_type ASC
        """
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        setup_reached = _safe_int(row["setup_reached"])
        result.append(
            {
                "zero_type": row["zero_type"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "in_progress": _safe_int(row["in_progress"]),
                "setup_reached": setup_reached,
                "success_rate": round(_pct(successes, finished), 2),
                "setup_reach_rate": round(_pct(setup_reached, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_streaks(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where.replace(' WHERE', ' AND')}
        ORDER BY id ASC
        """,
        params,
    )
    current_success_streak = 0
    best_success_streak = 0
    running = 0
    for row in rows:
        if row["status"] == "success":
            running += 1
            best_success_streak = max(best_success_streak, running)
        else:
            running = 0

    for row in reversed(rows):
        if row["status"] == "success":
            current_success_streak += 1
        else:
            break

    return {
        "current_success_streak": current_success_streak,
        "best_success_streak": best_success_streak,
    }


def compute_window_consistency(
    db: Database,
    window_size: int,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float | int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where.replace(' WHERE', ' AND')}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, window_size),
    )
    total = len(rows)
    successes = sum(1 for row in rows if row["status"] == "success")
    return {
        "window": window_size,
        "attempts": total,
        "successes": successes,
        "success_rate": round(_pct(successes, total), 2),
    }


def compute_summary(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    totals = db.query_one(
        f"""
        SELECT
            COUNT(*) AS total_attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(first_bed_seconds) AS avg_first_bed_seconds,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data,
            MIN(CASE WHEN status = 'success' THEN explosives_used END) AS best_rotations_success,
            MIN(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN explosives_used + COALESCE(explosives_left, 0)
                END
            ) AS best_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used = 2 AND explosives_left = 2
                    THEN 1
                    ELSE 0
                END
            ) AS perfect_2_2_count
        FROM attempts
        {where}
        """,
        params,
    )
    totals = totals or {}
    successes = _safe_int(totals["successes"])
    failures = _safe_int(totals["failures"])
    finished = successes + failures
    medians = compute_medians(
        db, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )
    recent = compute_recent_window(
        db, 20, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )

    return {
        "total_attempts": _safe_int(totals["total_attempts"]),
        "successes": successes,
        "failures": failures,
        "finished_attempts": finished,
        "success_rate": round(_pct(successes, finished), 2),
        "avg_first_bed_seconds": round(_safe_float(totals["avg_first_bed_seconds"]), 2),
        "avg_success_time_seconds": round(_safe_float(totals["avg_success_time"]), 2),
        "avg_damage_per_bed": round(_safe_float(totals["avg_damage_per_bed"]), 2),
        "avg_rotations_success": _round_or_none(totals["avg_rotations_success"]),
        "avg_total_explosives_success": _round_or_none(totals["avg_total_explosives_success"]),
        "successes_with_rotation_data": _safe_int(totals["successes_with_rotation_data"]),
        "successes_with_explosives_data": _safe_int(totals["successes_with_explosives_data"]),
        "best_rotations_success": _safe_int(totals["best_rotations_success"]),
        "best_total_explosives_success": _safe_int(totals["best_total_explosives_success"]),
        "perfect_2_2_count": _safe_int(totals["perfect_2_2_count"]),
        "perfect_2_2_rate_among_successes": round(
            _pct(_safe_int(totals["perfect_2_2_count"]), successes), 2
        ),
        "median_success_time_seconds": medians["median_success_time_seconds"],
        "median_damage_per_bed": medians["median_damage_per_bed"],
        "recent_success_rate": recent["success_rate"],
        "recent_avg_success_time_seconds": recent["avg_success_time_seconds"],
        "recent_avg_damage_per_bed": recent["avg_damage_per_bed"],
    }


def compute_damage_per_bed(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        WITH filtered_attempts AS (
            SELECT id
            FROM attempts
            {where}
        ),
        major_hits AS (
            SELECT
                b.attempt_id,
                b.damage,
                ROW_NUMBER() OVER (PARTITION BY b.attempt_id ORDER BY b.id ASC) AS major_hit_index
            FROM attempt_beds b
            JOIN filtered_attempts f ON f.id = b.attempt_id
            WHERE b.is_major = 1
        )
        SELECT
            major_hit_index AS bed_number,
            COUNT(*) AS hits,
            AVG(damage) AS avg_damage,
            MAX(damage) AS max_damage,
            MIN(damage) AS min_damage
        FROM major_hits
        GROUP BY major_hit_index
        ORDER BY major_hit_index ASC
        """,
        params,
    )
    return [
        {
            "bed_number": _safe_int(row["bed_number"]),
            "hits": _safe_int(row["hits"]),
            "avg_damage": round(_safe_float(row["avg_damage"]), 2),
            "max_damage": _safe_int(row["max_damage"]),
            "min_damage": _safe_int(row["min_damage"]),
        }
        for row in rows
    ]


def build_dashboard_payload_selected(
    db: Database,
    *,
    include_1_8: bool = False,
    rotation: str = "both",
    window: str = "all",
    tower_name: str | None = None,
    front_back: str | None = None,
    attempt_source: str = "practice",
    detail: str = "full",
) -> dict[str, Any]:
    detail = detail.lower().strip()
    if detail not in {"light", "full"}:
        detail = "full"
    rotation = rotation.lower().strip()
    if rotation not in {"both", "cw", "ccw"}:
        rotation = "both"
    window = window.lower().strip()
    if window not in {"all", "current_session", "last_10", "last_25", "last_50", "last_100"}:
        window = "all"
    attempt_source = attempt_source.lower().strip()
    if attempt_source not in {"all", "practice", "mpk"}:
        attempt_source = "all"

    tok_straight = INCLUDE_STRAIGHT_CTX.set(include_1_8)
    tok_rotation = ROTATION_FILTER_CTX.set(rotation)
    tok_source = ATTEMPT_SOURCE_CTX.set(attempt_source)
    try:
        bounds_by_window = _compute_window_bounds(db)
        bounds = bounds_by_window.get(window, {"min_id": None, "start_utc": None})
        tok_id = WINDOW_MIN_ID_CTX.set(bounds.get("min_id"))
        tok_start = WINDOW_START_UTC_CTX.set(bounds.get("start_utc"))
        try:
            tower_front_back_overview = compute_tower_front_back_overview(db)
            available_towers = sorted(
                {
                    row["tower_name"]
                    for row in tower_front_back_overview
                    if row["tower_name"] not in {"", "Unknown"}
                }
            )
            available_front_backs = sorted(
                {
                    row["front_back"]
                    for row in tower_front_back_overview
                    if row["front_back"] in {"Front", "Back"}
                }
            )

            window_options = [
                {"key": "all", "label": "All"},
                {"key": "current_session", "label": "Current Session"},
                {"key": "last_10", "label": "Last 10"},
                {"key": "last_25", "label": "Last 25"},
                {"key": "last_50", "label": "Last 50"},
                {"key": "last_100", "label": "Last 100"},
            ]

            scope: dict[str, Any] = {
                "summary": compute_summary(db, tower_name=tower_name, front_back=front_back),
                "streaks": compute_streaks(db, tower_name=tower_name, front_back=front_back),
                "bests": compute_best_and_recent(db, tower_name=tower_name, front_back=front_back),
                "consistency_windows": [
                    compute_window_consistency(db, 10, tower_name=tower_name, front_back=front_back),
                    compute_window_consistency(db, 25, tower_name=tower_name, front_back=front_back),
                    compute_window_consistency(db, 50, tower_name=tower_name, front_back=front_back),
                ],
            }

            if detail == "full":
                scope.update(
                    {
                        "damage_per_bed": compute_damage_per_bed(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "tower_performance": compute_tower_performance(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "tower_type_breakdown": compute_tower_type_breakdown(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "session_progression": compute_session_progression(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "time_series": compute_time_series(
                            db, limit=200, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_10": compute_rolling_consistency(
                            db, window_size=10, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_25": compute_rolling_consistency(
                            db, window_size=25, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "rolling_consistency_50": compute_rolling_consistency(
                            db, window_size=50, limit=400, tower_name=tower_name, front_back=front_back
                        ),
                        "outcome_runs": compute_outcome_runs(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "speed_bins": compute_speed_bins(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "attempts_by_session": compute_attempts_by_session(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "o_level_consistency": compute_o_level_consistency(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "o_level_heatmap": compute_o_level_heatmap(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "standing_height_consistency": compute_standing_height_consistency(
                            db, tower_name=tower_name, front_back=front_back
                        ),
                        "recent_attempts": compute_recent_attempts(
                            db, limit=60, tower_name=tower_name, front_back=front_back
                        ),
                    }
                )
            else:
                scope.update(
                    {
                        "damage_per_bed": [],
                        "tower_performance": [],
                        "tower_type_breakdown": [],
                        "session_progression": [],
                        "time_series": [],
                        "rolling_consistency_10": [],
                        "rolling_consistency_25": [],
                        "rolling_consistency_50": [],
                        "outcome_runs": {"runs": [], "best_success_run": 0, "best_fail_run": 0},
                        "speed_bins": [],
                        "attempts_by_session": [],
                        "o_level_consistency": [],
                        "o_level_heatmap": [],
                        "standing_height_consistency": [],
                        "recent_attempts": [],
                    }
                )

            payload = {
                "scope": scope,
                "tower_front_back_overview": tower_front_back_overview,
                "tower_radar": compute_tower_radar(db) if detail == "full" else {"front": [], "back": []},
                "available_towers": available_towers,
                "available_front_backs": available_front_backs,
                "window_options": window_options,
                "selected_window": window,
                "selected_rotation": rotation,
                "selected_include_1_8": include_1_8,
                "selected_attempt_source": attempt_source,
                "attempt_source_options": [
                    {"key": "all", "label": "All Data"},
                    {"key": "practice", "label": "Practice Map"},
                    {"key": "mpk", "label": "MPK Seeds"},
                ],
                "detail": detail,
                "practice_next": compute_practice_next_widget(db),
            }
            return payload
        finally:
            WINDOW_MIN_ID_CTX.reset(tok_id)
            WINDOW_START_UTC_CTX.reset(tok_start)
    finally:
        ROTATION_FILTER_CTX.reset(tok_rotation)
        INCLUDE_STRAIGHT_CTX.reset(tok_straight)
        ATTEMPT_SOURCE_CTX.reset(tok_source)


def compute_tower_performance(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used = 2 AND explosives_left = 2
                    THEN 1
                    ELSE 0
                END
            ) AS perfect_2_2_count
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back
        ORDER BY attempts DESC, tower_name ASC, front_back ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
                "avg_damage_per_bed": round(_safe_float(row["avg_damage_per_bed"]), 2),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
                "successes_with_rotation_data": _safe_int(row["successes_with_rotation_data"]),
                "successes_with_explosives_data": _safe_int(row["successes_with_explosives_data"]),
                "perfect_2_2_count": _safe_int(row["perfect_2_2_count"]),
            }
        )
    return result


def compute_tower_type_breakdown(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COALESCE(zero_type, 'Unknown') AS zero_type,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time,
            AVG(
                CASE
                    WHEN major_hit_count > 0 THEN CAST(major_damage_total AS REAL) / major_hit_count
                END
            ) AS avg_damage_per_bed,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_rotation_data,
            SUM(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS successes_with_explosives_data
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back, COALESCE(zero_type, 'Unknown')
        ORDER BY tower_name ASC, front_back ASC, attempts DESC, zero_type ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "zero_type": row["zero_type"],
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
                "avg_damage_per_bed": round(_safe_float(row["avg_damage_per_bed"]), 2),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
                "successes_with_rotation_data": _safe_int(row["successes_with_rotation_data"]),
                "successes_with_explosives_data": _safe_int(row["successes_with_explosives_data"]),
            }
        )
    return result


def compute_recent_attempts(
    db: Database,
    limit: int = 40,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    rows = db.query_all(
        f"""
        SELECT
            id,
            COALESCE(attempt_source, 'practice') AS attempt_source,
            status,
            fail_reason,
            started_at_utc,
            started_clock,
            ended_at_utc,
            ended_clock,
            first_bed_seconds,
            success_time_seconds,
            tower_name,
            tower_code,
            zero_type,
            explosives_used,
            explosives_left,
            bed_count,
            total_damage,
            major_damage_total,
            major_hit_count,
            o_level
        FROM attempts
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        bed_count = _safe_int(row["bed_count"])
        total_damage = _safe_int(row["total_damage"])
        major_hit_count = _safe_int(row["major_hit_count"])
        major_damage_per_hit = (
            _safe_float(row["major_damage_total"]) / major_hit_count if major_hit_count > 0 else 0.0
        )
        rotations = row["explosives_used"]
        explosives_left = row["explosives_left"]
        total_explosives = (
            (int(rotations) + (int(explosives_left) if explosives_left is not None else 0))
            if rotations is not None
            else None
        )
        result.append(
            {
                "id": _safe_int(row["id"]),
                "attempt_source": str(row["attempt_source"] or "practice"),
                "status": row["status"],
                "fail_reason": row["fail_reason"],
                "started_at_utc": row["started_at_utc"],
                "started_clock": row["started_clock"],
                "ended_at_utc": row["ended_at_utc"],
                "ended_clock": row["ended_clock"],
                "first_bed_seconds": round(_safe_float(row["first_bed_seconds"]), 2),
                "success_time_seconds": round(_safe_float(row["success_time_seconds"]), 2),
                "tower_name": row["tower_name"] or "Unknown",
                "tower_code": row["tower_code"],
                "zero_type": row["zero_type"] or "Unknown",
                "rotations": _safe_int(rotations) if rotations is not None else None,
                "explosives_left": _safe_int(explosives_left) if explosives_left is not None else None,
                "total_explosives": total_explosives,
                "is_perfect_2_2": rotations == 2 and explosives_left == 2,
                "bed_count": bed_count,
                "total_damage": total_damage,
                "major_hit_count": major_hit_count,
                "major_damage_per_hit": round(major_damage_per_hit, 2),
                "o_level": _safe_int(row["o_level"]) if row["o_level"] is not None else None,
            }
        )
    return result


def compute_session_progression(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        WITH scoped AS (
            SELECT
                id,
                started_at_utc,
                status,
                success_time_seconds,
                explosives_used,
                explosives_left
            FROM attempts
            WHERE status IN ('success', 'fail'){where}
            ORDER BY started_at_utc ASC, id ASC
        ),
        with_prev AS (
            SELECT
                *,
                LAG(started_at_utc) OVER (ORDER BY started_at_utc ASC, id ASC) AS prev_started_at_utc
            FROM scoped
        ),
        with_sessions AS (
            SELECT
                *,
                CASE
                    WHEN prev_started_at_utc IS NULL THEN 1
                    WHEN (julianday(started_at_utc) - julianday(prev_started_at_utc)) * 86400.0 > 3600.0 THEN 1
                    ELSE 0
                END AS is_new_session
            FROM with_prev
        ),
        numbered AS (
            SELECT
                *,
                SUM(is_new_session) OVER (ORDER BY started_at_utc ASC, id ASC) AS session_index
            FROM with_sessions
        )
        SELECT
            session_index,
            MIN(started_at_utc) AS session_start_utc,
            MAX(started_at_utc) AS session_last_attempt_utc,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time_seconds,
            AVG(CASE WHEN status = 'success' THEN CAST(explosives_used AS REAL) END) AS avg_rotations_success,
            AVG(
                CASE
                    WHEN status = 'success' AND explosives_used IS NOT NULL
                    THEN CAST(explosives_used + COALESCE(explosives_left, 0) AS REAL)
                END
            ) AS avg_total_explosives_success
        FROM numbered
        GROUP BY session_index
        ORDER BY session_index ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "session_index": _safe_int(row["session_index"]),
                "session_label": f"S{_safe_int(row['session_index'])}",
                "session_start_utc": row["session_start_utc"],
                "session_last_attempt_utc": row["session_last_attempt_utc"],
                "attempts": attempts,
                "successes": successes,
                "failures": _safe_int(row["failures"]),
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": _round_or_none(row["avg_success_time_seconds"]),
                "avg_rotations_success": _round_or_none(row["avg_rotations_success"]),
                "avg_total_explosives_success": _round_or_none(row["avg_total_explosives_success"]),
            }
        )
    return result


def compute_medians(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    success_times = [
        _safe_float(row["success_time_seconds"])
        for row in db.query_all(
            f"""
            SELECT success_time_seconds
            FROM attempts
            WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
            ORDER BY success_time_seconds ASC
            """,
            params,
        )
    ]
    first_beds = [
        _safe_float(row["first_bed_seconds"])
        for row in db.query_all(
            f"""
            SELECT first_bed_seconds
            FROM attempts
            WHERE first_bed_seconds IS NOT NULL{where_success}
            ORDER BY first_bed_seconds ASC
            """,
            params,
        )
    ]
    damage_per_bed = [
        _safe_float(row["damage_per_bed"])
        for row in db.query_all(
            f"""
            SELECT CAST(major_damage_total AS REAL) / major_hit_count AS damage_per_bed
            FROM attempts
            WHERE major_hit_count > 0{where_success}
            ORDER BY damage_per_bed ASC
            """,
            params,
        )
    ]

    def median(values: list[float]) -> float:
        if not values:
            return 0.0
        mid = len(values) // 2
        if len(values) % 2 == 0:
            return (values[mid - 1] + values[mid]) / 2
        return values[mid]

    return {
        "median_success_time_seconds": round(median(success_times), 2),
        "median_damage_per_bed": round(median(damage_per_bed), 2),
    }


def compute_recent_window(
    db: Database,
    window_size: int,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, float | int]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT
            status,
            first_bed_seconds,
            success_time_seconds,
            major_hit_count,
            major_damage_total
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, window_size),
    )
    attempts = len(rows)
    successes = sum(1 for row in rows if row["status"] == "success")
    avg_first_bed = [
        _safe_float(row["first_bed_seconds"]) for row in rows if row["first_bed_seconds"] is not None
    ]
    avg_success_time = [
        _safe_float(row["success_time_seconds"])
        for row in rows
        if row["status"] == "success" and row["success_time_seconds"] is not None
    ]
    damage_values = []
    for row in rows:
        major_hit_count = _safe_int(row["major_hit_count"])
        if major_hit_count > 0:
            damage_values.append(_safe_float(row["major_damage_total"]) / major_hit_count)
    return {
        "window": window_size,
        "attempts": attempts,
        "success_rate": round(_pct(successes, attempts), 2),
        "avg_first_bed_seconds": round(sum(avg_first_bed) / len(avg_first_bed), 2) if avg_first_bed else 0.0,
        "avg_success_time_seconds": round(sum(avg_success_time) / len(avg_success_time), 2)
        if avg_success_time
        else 0.0,
        "avg_damage_per_bed": round(sum(damage_values) / len(damage_values), 2) if damage_values else 0.0,
    }


def compute_time_series(
    db: Database,
    limit: int = 200,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT
            id,
            started_at_utc,
            status,
            first_bed_seconds,
            success_time_seconds,
            major_hit_count,
            major_damage_total
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        major_hit_count = _safe_int(row["major_hit_count"])
        major_damage_total = _safe_int(row["major_damage_total"])
        damage_per_bed = (major_damage_total / major_hit_count) if major_hit_count > 0 else 0.0
        result.append(
            {
                "id": _safe_int(row["id"]),
                "started_at_utc": row["started_at_utc"],
                "status": row["status"],
                "first_bed_seconds": round(_safe_float(row["first_bed_seconds"]), 2),
                "success_time_seconds": round(_safe_float(row["success_time_seconds"]), 2),
                "damage_per_bed": round(damage_per_bed, 2),
            }
        )
    return result


def compute_rolling_consistency(
    db: Database,
    window_size: int = 10,
    limit: int = 400,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT id, status
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    rows = list(reversed(rows))
    window: list[int] = []
    successes = 0
    result: list[dict[str, Any]] = []
    for row in rows:
        is_success = 1 if row["status"] == "success" else 0
        window.append(is_success)
        successes += is_success
        if len(window) > window_size:
            successes -= window.pop(0)
        attempts_in_window = len(window)
        result.append(
            {
                "id": _safe_int(row["id"]),
                "window_attempts": attempts_in_window,
                "rolling_success_rate": round(_pct(successes, attempts_in_window), 2),
                "is_full_window": attempts_in_window == window_size,
            }
        )
    return result


def compute_time_between_attempts(
    db: Database,
    limit: int = 200,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT id, started_at_utc
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    timestamps = [row["started_at_utc"] for row in reversed(rows)]
    result: list[dict[str, Any]] = []
    prev = None
    for idx, ts in enumerate(timestamps):
        delta = 0.0
        if prev and ts:
            try:
                prev_dt = prev
                curr_dt = ts
                delta = (
                    __import__("datetime")
                    .datetime.fromisoformat(curr_dt)
                    .timestamp()
                    - __import__("datetime").datetime.fromisoformat(prev_dt).timestamp()
                )
            except ValueError:
                delta = 0.0
        result.append({"index": idx + 1, "seconds_between": round(delta, 2)})
        prev = ts
    return result


def compute_outcome_runs(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_finished = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT status
        FROM attempts
        WHERE status IN ('success', 'fail'){where_finished}
        ORDER BY id ASC
        """,
        params,
    )
    runs: list[dict[str, Any]] = []
    if not rows:
        return {"runs": runs, "best_success_run": 0, "best_fail_run": 0}
    current_status = rows[0]["status"]
    current_len = 1
    best_success = 0
    best_fail = 0
    for row in rows[1:]:
        status = row["status"]
        if status == current_status:
            current_len += 1
        else:
            runs.append({"status": current_status, "length": current_len})
            if current_status == "success":
                best_success = max(best_success, current_len)
            else:
                best_fail = max(best_fail, current_len)
            current_status = status
            current_len = 1
    runs.append({"status": current_status, "length": current_len})
    if current_status == "success":
        best_success = max(best_success, current_len)
    else:
        best_fail = max(best_fail, current_len)
    return {"runs": runs, "best_success_run": best_success, "best_fail_run": best_fail}


def compute_speed_bins(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT success_time_seconds
        FROM attempts
        WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
        """,
        params,
    )
    bins = [
        {"label": "<28s", "min": 0, "max": 28, "count": 0},
        {"label": "28-30s", "min": 28, "max": 30, "count": 0},
        {"label": "30-32s", "min": 30, "max": 32, "count": 0},
        {"label": "32-35s", "min": 32, "max": 35, "count": 0},
        {"label": "35s+", "min": 35, "max": 10_000, "count": 0},
    ]
    for row in rows:
        value = _safe_float(row["success_time_seconds"])
        for bucket in bins:
            if bucket["min"] <= value < bucket["max"]:
                bucket["count"] += 1
                break
    return [{"label": b["label"], "count": b["count"]} for b in bins]


def compute_first_bed_bins(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_any = where.replace(" WHERE", " AND")
    rows = db.query_all(
        f"""
        SELECT first_bed_seconds
        FROM attempts
        WHERE first_bed_seconds IS NOT NULL{where_any}
        """,
        params,
    )
    bins = [
        {"label": "<14s", "min": 0, "max": 14, "count": 0},
        {"label": "14-16s", "min": 14, "max": 16, "count": 0},
        {"label": "16-18s", "min": 16, "max": 18, "count": 0},
        {"label": "18-20s", "min": 18, "max": 20, "count": 0},
        {"label": "20s+", "min": 20, "max": 10_000, "count": 0},
    ]
    for row in rows:
        value = _safe_float(row["first_bed_seconds"])
        for bucket in bins:
            if bucket["min"] <= value < bucket["max"]:
                bucket["count"] += 1
                break
    return [{"label": b["label"], "count": b["count"]} for b in bins]


def compute_best_and_recent(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=True
    )
    where_success = where.replace(" WHERE", " AND")
    best_success = db.query_one(
        f"""
        SELECT MIN(success_time_seconds) AS best_time
        FROM attempts
        WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
        """,
        params,
    )
    recent_success = db.query_one(
        f"""
        SELECT AVG(success_time_seconds) AS avg_time
        FROM (
            SELECT success_time_seconds
            FROM attempts
            WHERE status = 'success' AND success_time_seconds IS NOT NULL{where_success}
            ORDER BY id DESC
            LIMIT 20
        )
        """,
        params,
    )
    return {
        "best_success_time_seconds": round(_safe_float(best_success["best_time"] if best_success else 0.0), 2),
        "recent_success_time_seconds": round(
            _safe_float(recent_success["avg_time"] if recent_success else 0.0), 2
        ),
    }


def compute_attempts_by_session(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    sessions = compute_session_progression(
        db, zero_type=zero_type, tower_name=tower_name, front_back=front_back
    )
    return [
        {
            "session_label": row["session_label"],
            "attempts": row["attempts"],
            "successes": row["successes"],
            "success_rate": row["success_rate"],
        }
        for row in sessions
    ]


def compute_o_level_consistency(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            o_level,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL{where}
        GROUP BY o_level
        ORDER BY o_level ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "o_level": _safe_int(row["o_level"]),
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_o_level_heatmap(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
    *,
    default_min: int = 52,
    default_max: int = 59,
    hard_min: int = 45,
    hard_max: int = 70,
) -> dict[str, Any]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            o_level,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where}
        GROUP BY COALESCE(tower_name, 'Unknown'), side, o_level
        ORDER BY side ASC, tower_name ASC, o_level ASC
        """,
        params,
    )
    standing_rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS side,
            o_level,
            standing_height,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND o_level IS NOT NULL
          AND standing_height IS NOT NULL
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where}
        GROUP BY COALESCE(tower_name, 'Unknown'), side, o_level, standing_height
        ORDER BY side ASC, tower_name ASC, o_level ASC, standing_height ASC
        """,
        params,
    )
    expected_towers = [
        "Small Boy",
        "Small Cage",
        "Tall Cage",
        "M-85",
        "M-88",
        "M-91",
        "T-94",
        "T-97",
        "T-100",
        "Tall Boy",
    ]
    side_order = {"Front": 0, "Back": 1}
    tower_order = {name: idx for idx, name in enumerate(expected_towers)}

    def _allowed_combo(combo_tower: str, combo_side: str) -> bool:
        if combo_side not in {"Front", "Back"}:
            return False
        if tower_name is not None and combo_tower != tower_name:
            return False
        if front_back is not None and combo_side != front_back:
            return False
        return True

    by_combo_level: dict[tuple[str, str], dict[int, dict[str, int]]] = {}
    standing_by_combo_level: dict[tuple[str, str], dict[int, list[dict[str, int]]]] = {}
    min_seen: int | None = None
    max_seen: int | None = None
    for row in rows:
        combo_tower = str(row["tower_name"])
        combo_side = str(row["side"])
        if not _allowed_combo(combo_tower, combo_side):
            continue
        level = _safe_int(row["o_level"])
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        combo_key = (combo_tower, combo_side)
        if combo_key not in by_combo_level:
            by_combo_level[combo_key] = {}
        by_combo_level[combo_key][level] = {"attempts": attempts, "successes": successes}
        min_seen = level if min_seen is None else min(min_seen, level)
        max_seen = level if max_seen is None else max(max_seen, level)
    for row in standing_rows:
        combo_tower = str(row["tower_name"])
        combo_side = str(row["side"])
        if not _allowed_combo(combo_tower, combo_side):
            continue
        level = _safe_int(row["o_level"])
        standing = _safe_int(row["standing_height"])
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        combo_key = (combo_tower, combo_side)
        if combo_key not in standing_by_combo_level:
            standing_by_combo_level[combo_key] = {}
        if level not in standing_by_combo_level[combo_key]:
            standing_by_combo_level[combo_key][level] = []
        standing_by_combo_level[combo_key][level].append(
            {
                "standing_height": standing,
                "attempts": attempts,
                "successes": successes,
            }
        )

    if min_seen is None or max_seen is None:
        min_level = default_min
        max_level = default_max
    else:
        min_level = max(hard_min, min(default_min, min_seen))
        max_level = min(hard_max, max(default_max, max_seen))

    o_levels = list(range(min_level, max_level + 1))

    combo_keys: set[tuple[str, str]] = set()
    for side in ("Front", "Back"):
        for expected_tower in expected_towers:
            if _allowed_combo(expected_tower, side):
                combo_keys.add((expected_tower, side))
    combo_keys.update(by_combo_level.keys())
    combo_keys = {combo for combo in combo_keys if _allowed_combo(combo[0], combo[1])}
    sorted_combos = sorted(
        combo_keys,
        key=lambda combo: (
            side_order.get(combo[1], 99),
            tower_order.get(combo[0], 999),
            combo[0],
        ),
    )

    matrix_rows: list[dict[str, Any]] = []
    for combo_tower, combo_side in sorted_combos:
        level_buckets = by_combo_level.get((combo_tower, combo_side), {})
        standing_buckets = standing_by_combo_level.get((combo_tower, combo_side), {})
        cells: list[dict[str, Any]] = []
        row_attempts = 0
        row_successes = 0
        for level in o_levels:
            bucket = level_buckets.get(level, {"attempts": 0, "successes": 0})
            attempts = int(bucket["attempts"])
            successes = int(bucket["successes"])
            row_attempts += attempts
            row_successes += successes
            cells.append(
                {
                    "o_level": level,
                    "attempts": attempts,
                    "successes": successes,
                    "success_rate": round(_pct(successes, attempts), 2) if attempts > 0 else None,
                    "standing_height_breakdown": standing_buckets.get(level, []),
                }
            )
        matrix_rows.append(
            {
                "tower_name": combo_tower,
                "side": combo_side,
                "label": f"{combo_side} {combo_tower}",
                "attempts": row_attempts,
                "successes": row_successes,
                "success_rate": round(_pct(row_successes, row_attempts), 2)
                if row_attempts > 0
                else None,
                "cells": cells,
            }
        )

    return {
        "o_levels": o_levels,
        "rows": matrix_rows,
    }


def compute_standing_height_consistency(
    db: Database,
    zero_type: str | None = None,
    tower_name: str | None = None,
    front_back: str | None = None,
) -> list[dict[str, Any]]:
    where, params = _scope_where(
        zero_type=zero_type, tower_name=tower_name, front_back=front_back, include_where=False
    )
    rows = db.query_all(
        f"""
        SELECT
            standing_height,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND standing_height IS NOT NULL{where}
        GROUP BY standing_height
        ORDER BY standing_height ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        attempts = _safe_int(row["attempts"])
        successes = _safe_int(row["successes"])
        result.append(
            {
                "standing_height": _safe_int(row["standing_height"]),
                "attempts": attempts,
                "successes": successes,
                "success_rate": round(_pct(successes, attempts), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_tower_front_back_overview(db: Database) -> list[dict[str, Any]]:
    where, params = _scope_where(include_where=True)
    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
            AVG(CASE WHEN status = 'success' THEN success_time_seconds END) AS avg_success_time
        FROM attempts
        {where}
        GROUP BY COALESCE(tower_name, 'Unknown'), front_back
        ORDER BY attempts DESC, tower_name ASC, front_back ASC
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        successes = _safe_int(row["successes"])
        failures = _safe_int(row["failures"])
        finished = successes + failures
        result.append(
            {
                "tower_name": row["tower_name"],
                "front_back": row["front_back"],
                "attempts": _safe_int(row["attempts"]),
                "successes": successes,
                "failures": failures,
                "success_rate": round(_pct(successes, finished), 2),
                "avg_success_time_seconds": round(_safe_float(row["avg_success_time"]), 2),
            }
        )
    return result


def compute_tower_radar(db: Database) -> dict[str, Any]:
    where_extra, params = _scope_where(include_where=False)
    # Keep a stable tower list so both Front/Back charts always show all known towers.
    all_towers_rows = db.query_all(
        f"""
        SELECT DISTINCT COALESCE(tower_name, 'Unknown') AS tower_name
        FROM attempts
        WHERE COALESCE(tower_name, 'Unknown') <> 'Unknown'{where_extra}
        ORDER BY tower_name ASC
        """,
        params,
    )
    all_towers = [str(r["tower_name"]) for r in all_towers_rows]

    rows = db.query_all(
        f"""
        SELECT
            COALESCE(tower_name, 'Unknown') AS tower_name,
            CASE
                WHEN COALESCE(zero_type, '') LIKE 'Front %' THEN 'Front'
                WHEN COALESCE(zero_type, '') LIKE 'Back %' THEN 'Back'
                ELSE 'Unknown'
            END AS front_back,
            status,
            explosives_used,
            explosives_left
        FROM attempts
        WHERE status IN ('success', 'fail')
          AND COALESCE(tower_name, 'Unknown') <> 'Unknown'{where_extra}
        """,
        params,
    )

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        side = str(row["front_back"])
        if side not in {"Front", "Back"}:
            continue
        tower = str(row["tower_name"])
        key = (tower, side)
        bucket = grouped.setdefault(
            key,
            {
                "attempt_count": 0,
                "success_count": 0,
                "explosives_success": [],
            },
        )
        bucket["attempt_count"] += 1
        if row["status"] == "success":
            bucket["success_count"] += 1
            used = row["explosives_used"]
            if used is not None:
                total = int(used)
                left = row["explosives_left"]
                if left is not None:
                    total += int(left)
                bucket["explosives_success"].append(total)

    def median(values: list[int]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 0:
            return (ordered[mid - 1] + ordered[mid]) / 2.0
        return float(ordered[mid])

    by_side: dict[str, list[dict[str, Any]]] = {"Front": [], "Back": []}
    for side in ("Front", "Back"):
        for tower in all_towers:
            bucket = grouped.get((tower, side), None)
            attempts = int(bucket["attempt_count"]) if bucket else 0
            successes = int(bucket["success_count"]) if bucket else 0
            success_rate = round(_pct(successes, attempts), 2)
            median_expl = round(median(bucket["explosives_success"]) if bucket else 0.0, 2)
            by_side[side].append(
                {
                    "tower_name": tower,
                    "attempt_count": attempts,
                    "success_rate": min(100.0, max(0.0, success_rate)),
                    "median_explosives": min(8.0, max(0.0, median_expl)),
                }
            )

    front_attempt_max = max((row["attempt_count"] for row in by_side["Front"]), default=0)
    back_attempt_max = max((row["attempt_count"] for row in by_side["Back"]), default=0)
    front_attempt_cap = max(1, front_attempt_max)
    back_attempt_cap = max(1, back_attempt_max)

    # Apply requested cap: attempt count is capped at highest attempt count in that side.
    for row in by_side["Front"]:
        row["attempt_count_capped"] = min(front_attempt_cap, row["attempt_count"])
    for row in by_side["Back"]:
        row["attempt_count_capped"] = min(back_attempt_cap, row["attempt_count"])

    return {
        "front": by_side["Front"],
        "back": by_side["Back"],
        "front_attempt_cap": front_attempt_cap,
        "back_attempt_cap": back_attempt_cap,
    }


def build_dashboard_payload(db: Database) -> dict[str, Any]:
    def scoped(tower_name: str | None = None, front_back: str | None = None) -> dict[str, Any]:
        return {
            "summary": compute_summary(db, tower_name=tower_name, front_back=front_back),
            "streaks": compute_streaks(db, tower_name=tower_name, front_back=front_back),
            "bests": compute_best_and_recent(db, tower_name=tower_name, front_back=front_back),
            "consistency_windows": [
                compute_window_consistency(db, 10, tower_name=tower_name, front_back=front_back),
                compute_window_consistency(db, 25, tower_name=tower_name, front_back=front_back),
                compute_window_consistency(db, 50, tower_name=tower_name, front_back=front_back),
            ],
            "damage_per_bed": compute_damage_per_bed(db, tower_name=tower_name, front_back=front_back),
            "tower_performance": compute_tower_performance(
                db, tower_name=tower_name, front_back=front_back
            ),
            "tower_type_breakdown": compute_tower_type_breakdown(
                db, tower_name=tower_name, front_back=front_back
            ),
            "session_progression": compute_session_progression(
                db, tower_name=tower_name, front_back=front_back
            ),
            "time_series": compute_time_series(
                db, limit=200, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_10": compute_rolling_consistency(
                db, window_size=10, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_25": compute_rolling_consistency(
                db, window_size=25, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "rolling_consistency_50": compute_rolling_consistency(
                db, window_size=50, limit=400, tower_name=tower_name, front_back=front_back
            ),
            "outcome_runs": compute_outcome_runs(db, tower_name=tower_name, front_back=front_back),
            "speed_bins": compute_speed_bins(db, tower_name=tower_name, front_back=front_back),
            "attempts_by_session": compute_attempts_by_session(
                db, tower_name=tower_name, front_back=front_back
            ),
            "recent_attempts": compute_recent_attempts(
                db, limit=60, tower_name=tower_name, front_back=front_back
            ),
        }
    def build_scoped_payload_for_window(window_key: str, bounds: dict[str, Any]) -> dict[str, Any]:
        tok_id = WINDOW_MIN_ID_CTX.set(bounds.get("min_id"))
        tok_start = WINDOW_START_UTC_CTX.set(bounds.get("start_utc"))
        try:
            tower_front_back_overview = compute_tower_front_back_overview(db)
            available_towers = sorted(
                {
                    row["tower_name"]
                    for row in tower_front_back_overview
                    if row["tower_name"] not in {"", "Unknown"}
                }
            )
            available_front_backs = sorted(
                {
                    row["front_back"]
                    for row in tower_front_back_overview
                    if row["front_back"] in {"Front", "Back"}
                }
            )

            by_tower: dict[str, Any] = {}
            for tower_name in available_towers:
                by_tower[tower_name] = scoped(tower_name=tower_name)

            by_front_back: dict[str, Any] = {}
            for front_back in available_front_backs:
                by_front_back[front_back] = scoped(front_back=front_back)

            by_tower_front_back: dict[str, Any] = {}
            for tower_name in available_towers:
                for front_back in available_front_backs:
                    by_tower_front_back[f"{tower_name}|{front_back}"] = scoped(
                        tower_name=tower_name, front_back=front_back
                    )

            return {
                "global": scoped(None),
                "tower_front_back_overview": tower_front_back_overview,
                "tower_radar": compute_tower_radar(db),
                "available_towers": available_towers,
                "available_front_backs": available_front_backs,
                "by_tower": by_tower,
                "by_front_back": by_front_back,
                "by_tower_front_back": by_tower_front_back,
                "window_key": window_key,
            }
        finally:
            WINDOW_MIN_ID_CTX.reset(tok_id)
            WINDOW_START_UTC_CTX.reset(tok_start)

    def build_mode_payload(include_straight: bool, rotation_filter: str) -> dict[str, Any]:
        tok_straight = INCLUDE_STRAIGHT_CTX.set(include_straight)
        tok_rotation = ROTATION_FILTER_CTX.set(rotation_filter)
        try:
            window_order = ["all", "current_session", "last_10", "last_25", "last_50", "last_100"]
            window_labels = {
                "all": "All",
                "current_session": "Current Session",
                "last_10": "Last 10",
                "last_25": "Last 25",
                "last_50": "Last 50",
                "last_100": "Last 100",
            }
            bounds_by_window = _compute_window_bounds(db)
            windows_payload: dict[str, Any] = {}
            for key in window_order:
                windows_payload[key] = build_scoped_payload_for_window(key, bounds_by_window.get(key, {}))

            all_payload = windows_payload["all"]
            return {
                **all_payload,
                "windows": windows_payload,
                "window_options": [{"key": key, "label": window_labels[key]} for key in window_order],
                "default_window": "all",
            }
        finally:
            ROTATION_FILTER_CTX.reset(tok_rotation)
            INCLUDE_STRAIGHT_CTX.reset(tok_straight)

    mode_payloads: dict[str, Any] = {}
    for mode_key, include_straight in (("exclude_1_8", False), ("include_1_8", True)):
        rotation_modes: dict[str, Any] = {}
        for rotation_key in ("both", "cw", "ccw"):
            rotation_modes[rotation_key] = build_mode_payload(include_straight, rotation_key)
        mode_payloads[mode_key] = {
            "rotation_modes": rotation_modes,
            "default_rotation": "both",
        }

    default_payload = mode_payloads["exclude_1_8"]["rotation_modes"]["both"]
    practice_next = compute_practice_next_widget(db)
    return {
        **default_payload,
        "modes": mode_payloads,
        "default_include_1_8": False,
        "default_rotation": "both",
        "practice_next": practice_next,
    }
