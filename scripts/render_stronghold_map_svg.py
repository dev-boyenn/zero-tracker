from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _rgb_hex(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _visit_seconds_by_room(payload: dict[str, Any]) -> dict[int, float]:
    visits = payload.get("visits", [])
    out: dict[int, float] = {}
    if not isinstance(visits, list):
        return out
    for visit in visits:
        if not isinstance(visit, dict):
            continue
        room_id = int(visit.get("room_id", -1) or -1)
        if room_id < 0:
            continue
        seconds = float(visit.get("duration_seconds", 0.0) or 0.0)
        out[room_id] = out.get(room_id, 0.0) + seconds
    return out


def _room_fill_for_seconds(seconds: float, max_seconds: float, visited: bool) -> str:
    if not visited:
        return "#1d2e40"
    if max_seconds <= 0.0:
        return "#3a8d56"
    t = _clamp01(seconds / max_seconds)
    # Green -> yellow -> red
    if t < 0.5:
        u = t / 0.5
        r = _lerp(48, 201, u)
        g = _lerp(212, 200, u)
        b = _lerp(93, 72, u)
    else:
        u = (t - 0.5) / 0.5
        r = _lerp(201, 225, u)
        g = _lerp(200, 66, u)
        b = _lerp(72, 66, u)
    return _rgb_hex(int(r), int(g), int(b))


def _room_icon_spec(room_type: str, *, is_wide: bool) -> dict[str, Any]:
    t = str(room_type or "Unknown")
    if t in ("Corridor", "SmallCorridor"):
        return {
            "lines": [((0.14, 0.50), (0.86, 0.50))] if is_wide else [((0.50, 0.14), (0.50, 0.86))],
            "rects": [],
            "circles": [],
        }
    if t == "LeftTurn":
        return {
            "lines": [((0.22, 0.78), (0.22, 0.38)), ((0.22, 0.78), (0.66, 0.78))],
            "rects": [],
            "circles": [],
        }
    if t == "RightTurn":
        return {
            "lines": [((0.78, 0.78), (0.78, 0.38)), ((0.34, 0.78), (0.78, 0.78))],
            "rects": [],
            "circles": [],
        }
    if t == "FiveWayCrossing":
        return {
            "lines": [
                ((0.50, 0.14), (0.50, 0.86)),
                ((0.14, 0.50), (0.86, 0.50)),
                ((0.50, 0.50), (0.26, 0.26)),
                ((0.50, 0.50), (0.74, 0.26)),
            ],
            "rects": [],
            "circles": [],
        }
    if t == "Stairs":
        return {
            "lines": [
                ((0.18, 0.76), (0.38, 0.76)),
                ((0.38, 0.76), (0.38, 0.58)),
                ((0.38, 0.58), (0.58, 0.58)),
                ((0.58, 0.58), (0.58, 0.40)),
                ((0.58, 0.40), (0.78, 0.40)),
            ],
            "rects": [],
            "circles": [],
        }
    if t in ("SpiralStaircase", "Start"):
        return {
            "lines": [
                ((0.50, 0.20), (0.68, 0.34)),
                ((0.68, 0.34), (0.62, 0.56)),
                ((0.62, 0.56), (0.44, 0.62)),
                ((0.44, 0.62), (0.34, 0.50)),
                ((0.34, 0.50), (0.42, 0.40)),
                ((0.42, 0.40), (0.52, 0.44)),
            ],
            "rects": [((0.20, 0.20), 0.60, 0.60)],
            "circles": [],
        }
    if t == "PrisonHall":
        return {
            "lines": [
                ((0.36, 0.24), (0.36, 0.76)),
                ((0.50, 0.24), (0.50, 0.76)),
                ((0.64, 0.24), (0.64, 0.76)),
            ],
            "rects": [((0.18, 0.20), 0.64, 0.60)],
            "circles": [],
        }
    if t == "ChestCorridor":
        return {
            "lines": [((0.16, 0.50), (0.84, 0.50))] if is_wide else [((0.50, 0.16), (0.50, 0.84))],
            "rects": [((0.42, 0.38), 0.16, 0.16)],
            "circles": [],
        }
    if t == "SquareRoom":
        return {
            "lines": [],
            "rects": [((0.24, 0.24), 0.52, 0.52)],
            "circles": [((0.50, 0.50), 0.05)],
        }
    if t == "Library":
        return {
            "lines": [
                ((0.50, 0.24), (0.50, 0.76)),
                ((0.20, 0.28), (0.50, 0.24)),
                ((0.20, 0.72), (0.50, 0.76)),
                ((0.80, 0.28), (0.50, 0.24)),
                ((0.80, 0.72), (0.50, 0.76)),
            ],
            "rects": [],
            "circles": [],
        }
    if t == "PortalRoom":
        return {
            "lines": [],
            "rects": [((0.18, 0.20), 0.64, 0.60), ((0.34, 0.36), 0.32, 0.28)],
            "circles": [],
        }
    return {
        "lines": [((0.22, 0.22), (0.78, 0.78)), ((0.22, 0.78), (0.78, 0.22))],
        "rects": [],
        "circles": [],
    }


def _facing_quarter_turns_for_mirrored_map(facing: str) -> int:
    # Map is mirrored on horizontal axis, so east/west are swapped visually.
    f = str(facing or "NONE").upper()
    if f == "NORTH":
        return 0
    if f == "EAST":
        return 3
    if f == "SOUTH":
        return 2
    if f == "WEST":
        return 1
    return 0


def _rotate_unit_point(x: float, y: float, turns: int) -> tuple[float, float]:
    t = int(turns) % 4
    cx = 0.5
    cy = 0.5
    dx = float(x) - cx
    dy = float(y) - cy
    if t == 0:
        return (cx + dx, cy + dy)
    if t == 1:
        # 90° clockwise in SVG coordinates (y down).
        return (cx + dy, cy - dx)
    if t == 2:
        return (cx - dx, cy - dy)
    return (cx - dy, cy + dx)


def render_map_svg(
    json_path: Path,
    output_svg: Path | None = None,
    *,
    width: int = 1800,
    height: int = 1200,
) -> Path:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    pieces = payload.get("pieces", [])
    path_points = payload.get("path", [])
    visits = payload.get("visits", [])
    starter = payload.get("starter", {})

    rooms: list[dict[str, Any]] = []
    for piece in pieces:
        if not isinstance(piece, dict):
            continue
        required = ("id", "min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
        if any(k not in piece for k in required):
            continue
        try:
            room = {
                "id": int(piece.get("id", -1)),
                "type": str(piece.get("type", "Unknown")),
                "facing": str(piece.get("facing", "NONE")),
                "min_x": float(piece.get("min_x")),
                "max_x": float(piece.get("max_x")),
                "min_y": float(piece.get("min_y")),
                "max_y": float(piece.get("max_y")),
                "min_z": float(piece.get("min_z")),
                "max_z": float(piece.get("max_z")),
            }
        except Exception:
            continue
        rooms.append(room)
    if not rooms:
        raise ValueError(f"No room geometry found in JSON: {json_path}")

    min_x = min(room["min_x"] for room in rooms)
    max_x = max(room["max_x"] for room in rooms)
    min_z = min(room["min_z"] for room in rooms)
    max_z = max(room["max_z"] for room in rooms)

    margin = 60.0
    header = 110.0
    map_w = max(200.0, float(width) - 2.0 * margin)
    map_h = max(200.0, float(height) - (2.0 * margin + header))
    span_x = max(1.0, max_x - min_x)
    span_z = max(1.0, max_z - min_z)
    scale = min(map_w / span_x, map_h / span_z)

    def sx(x: float) -> float:
        # Horizontal mirror so rendered left/right matches expected in-world orientation.
        return margin + (max_x - x) * scale

    def sy(z: float) -> float:
        # z increases downward in world topdown; invert for chart.
        return margin + header + (max_z - z) * scale

    room_seconds = _visit_seconds_by_room(payload)
    visited_ids = set(room_seconds.keys())
    max_seconds = max(room_seconds.values()) if room_seconds else 0.0
    starter_id = int(starter.get("room_id", -1) or -1)
    connections_raw = payload.get("connections", [])
    connections: list[dict[str, Any]] = []
    if isinstance(connections_raw, list):
        for conn in connections_raw:
            if not isinstance(conn, dict):
                continue
            try:
                connections.append(
                    {
                        "a": int(conn.get("a", -1)),
                        "b": int(conn.get("b", -1)),
                        "axis": str(conn.get("axis", "")),
                        "x": float(conn.get("x")),
                        "z": float(conn.get("z")),
                        "y": int(conn.get("y", 0) or 0),
                        "kind": str(conn.get("kind", "open")),
                    }
                )
            except Exception:
                continue
    doors_raw = payload.get("doors", [])
    doors: list[dict[str, Any]] = []
    if isinstance(doors_raw, list):
        for door in doors_raw:
            if not isinstance(door, dict):
                continue
            try:
                doors.append(
                    {
                        "x": float(door.get("x")),
                        "z": float(door.get("z")),
                        "y": int(door.get("y", 0) or 0),
                        "type": str(door.get("type", "oak")),
                        "facing": str(door.get("facing", "unknown")).lower(),
                    }
                )
            except Exception:
                continue
    chests_raw = payload.get("chests", [])
    chests: list[dict[str, Any]] = []
    if isinstance(chests_raw, list):
        for chest in chests_raw:
            if not isinstance(chest, dict):
                continue
            try:
                chests.append(
                    {
                        "x": float(chest.get("x")),
                        "z": float(chest.get("z")),
                        "y": int(chest.get("y", 0) or 0),
                        "type": str(chest.get("type", "chest")),
                        "room_id": int(chest.get("room_id", -1) or -1),
                        "room_type": str(chest.get("room_type", "Unknown")),
                    }
                )
            except Exception:
                continue

    poly_points: list[tuple[float, float]] = []
    raw_path_points: list[dict[str, Any]] = []
    for point in path_points:
        if isinstance(point, dict):
            raw_path_points.append(point)

    overworld_path_points = [p for p in raw_path_points if int(p.get("dim", 0) or 0) == 0]
    draw_points = overworld_path_points if overworld_path_points else raw_path_points

    for point in draw_points:
        if not isinstance(point, dict):
            continue
        try:
            x = float(point.get("x"))
            z = float(point.get("z"))
            poly_points.append((sx(x), sy(z)))
        except Exception:
            continue

    if output_svg is None:
        output_svg = json_path.with_suffix(".svg")
    output_svg.parent.mkdir(parents=True, exist_ok=True)

    title = f"Stronghold Topdown - {json_path.parent.parent.name}"
    subtitle = (
        f"Rooms: {len(rooms)} | Visited: {len(visited_ids)} | "
        f"Starter(FiveWay): {starter_id} | Connections: {len(connections)} | Doors(oak/iron): {len(doors)} | "
        f"Chests: {len(chests)} | Max room time: {max_seconds:.2f}s"
    )

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
    )
    lines.append("  <defs>")
    lines.append("    <style>")
    lines.append("      .title { font: 700 28px Segoe UI, Arial, sans-serif; fill: #ecf5ff; }")
    lines.append("      .sub { font: 400 15px Segoe UI, Arial, sans-serif; fill: #9ec6ea; }")
    lines.append("      .lbl { font: 600 11px Segoe UI, Arial, sans-serif; fill: #0b121c; }")
    lines.append("      .legend { font: 600 13px Segoe UI, Arial, sans-serif; fill: #d7ebff; }")
    lines.append("      .legendSmall { font: 400 12px Segoe UI, Arial, sans-serif; fill: #b8d5f0; }")
    lines.append("      .keyTag { font: 700 12px Segoe UI, Arial, sans-serif; fill: #f4fbff; }")
    lines.append("    </style>")
    lines.append("  </defs>")
    lines.append(f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#071a2a"/>')
    lines.append(
        f'  <rect x="{margin-10:.1f}" y="{margin+header-10:.1f}" width="{map_w+20:.1f}" '
        f'height="{map_h+20:.1f}" rx="12" fill="#0a2438" stroke="#1a4566" stroke-width="2"/>'
    )
    lines.append(f'  <text x="{margin:.1f}" y="{margin:.1f}" class="title">{_escape_xml(title)}</text>')
    lines.append(f'  <text x="{margin:.1f}" y="{margin+32:.1f}" class="sub">{_escape_xml(subtitle)}</text>')

    # Draw rooms in vertical order: lower Y first, higher Y on top.
    rooms_draw_order = sorted(
        rooms,
        key=lambda r: ((r["min_y"] + r["max_y"]) * 0.5, int(r["id"])),
    )
    for room in rooms_draw_order:
        x0 = sx(room["min_x"])
        x1 = sx(room["max_x"])
        y0 = sy(room["max_z"])
        y1 = sy(room["min_z"])
        rx = min(x0, x1)
        ry = min(y0, y1)
        rw = max(1.0, abs(x1 - x0))
        rh = max(1.0, abs(y1 - y0))
        room_id = int(room["id"])
        secs = room_seconds.get(room_id, 0.0)
        is_visited = room_id in visited_ids
        fill = _room_fill_for_seconds(secs, max_seconds, is_visited)
        stroke = "#2d5a7e"
        if room["type"] == "PortalRoom":
            stroke = "#f0d37a"
        elif room["type"] == "FiveWayCrossing":
            stroke = "#78d6ff"
        stroke_w = "2.5" if room_id == starter_id else "1.1"
        lines.append(
            f'  <rect x="{rx:.2f}" y="{ry:.2f}" width="{rw:.2f}" height="{rh:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w}">'
            f"<title>#{room_id} {room['type']} | {secs:.2f}s</title></rect>"
        )

        # Room glyphs: minimal icon per room type (kept subtle and unobtrusive).
        icon_size = min(rw, rh) * 0.62
        if icon_size >= 7.0:
            cx = rx + rw * 0.5
            cy = ry + rh * 0.5
            ox = cx - icon_size * 0.5
            oy = cy - icon_size * 0.5
            icon = _room_icon_spec(str(room["type"]), is_wide=(rw >= rh))
            turns = _facing_quarter_turns_for_mirrored_map(str(room.get("facing", "NONE")))
            if str(room.get("type", "")) == "FiveWayCrossing":
                # Empirical correction: 5-way glyph orientation needs a 180° flip.
                turns = (turns + 2) % 4
            icon_sw = max(0.9, min(1.7, icon_size * 0.055))
            if is_visited:
                base_color = "#06111d"
                base_opacity = "0.68"
                hi_color = "#f4fbff"
                hi_opacity = "0.45"
                hi_sw = max(0.55, icon_sw * 0.45)
            else:
                base_color = "#c7def3"
                base_opacity = "0.30"
                hi_color = ""
                hi_opacity = "0.0"
                hi_sw = 0.0

            for (ax, ay), (bx, by) in icon.get("lines", []):
                rax, ray = _rotate_unit_point(float(ax), float(ay), turns)
                rbx, rby = _rotate_unit_point(float(bx), float(by), turns)
                x1 = ox + float(rax) * icon_size
                y1 = oy + float(ray) * icon_size
                x2 = ox + float(rbx) * icon_size
                y2 = oy + float(rby) * icon_size
                lines.append(
                    f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                    f'stroke="{base_color}" stroke-opacity="{base_opacity}" stroke-width="{icon_sw:.2f}" stroke-linecap="round" />'
                )
                if is_visited:
                    lines.append(
                        f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                        f'stroke="{hi_color}" stroke-opacity="{hi_opacity}" stroke-width="{hi_sw:.2f}" stroke-linecap="round" />'
                    )
            for (rxn, ryn), rwn, rhn in icon.get("rects", []):
                c1 = _rotate_unit_point(float(rxn), float(ryn), turns)
                c2 = _rotate_unit_point(float(rxn) + float(rwn), float(ryn), turns)
                c3 = _rotate_unit_point(float(rxn) + float(rwn), float(ryn) + float(rhn), turns)
                c4 = _rotate_unit_point(float(rxn), float(ryn) + float(rhn), turns)
                xs = [c1[0], c2[0], c3[0], c4[0]]
                ys = [c1[1], c2[1], c3[1], c4[1]]
                rr_x = ox + min(xs) * icon_size
                rr_y = oy + min(ys) * icon_size
                rr_w = max(0.8, (max(xs) - min(xs)) * icon_size)
                rr_h = max(0.8, (max(ys) - min(ys)) * icon_size)
                lines.append(
                    f'  <rect x="{rr_x:.2f}" y="{rr_y:.2f}" width="{rr_w:.2f}" height="{rr_h:.2f}" '
                    f'fill="none" stroke="{base_color}" stroke-opacity="{base_opacity}" stroke-width="{icon_sw:.2f}" />'
                )
                if is_visited:
                    lines.append(
                        f'  <rect x="{rr_x:.2f}" y="{rr_y:.2f}" width="{rr_w:.2f}" height="{rr_h:.2f}" '
                        f'fill="none" stroke="{hi_color}" stroke-opacity="{hi_opacity}" stroke-width="{hi_sw:.2f}" />'
                    )
            for (ccx, ccy), rr in icon.get("circles", []):
                rcx, rcy = _rotate_unit_point(float(ccx), float(ccy), turns)
                c_x = ox + float(rcx) * icon_size
                c_y = oy + float(rcy) * icon_size
                r = max(0.8, float(rr) * icon_size)
                lines.append(
                    f'  <circle cx="{c_x:.2f}" cy="{c_y:.2f}" r="{r:.2f}" '
                    f'fill="{base_color}" fill-opacity="{base_opacity}" stroke="none" />'
                )
                if is_visited:
                    lines.append(
                        f'  <circle cx="{c_x:.2f}" cy="{c_y:.2f}" r="{max(0.6, r * 0.55):.2f}" '
                        f'fill="{hi_color}" fill-opacity="{hi_opacity}" stroke="none" />'
                    )

        if is_visited:
            cx = rx + rw * 0.5
            cy = ry + rh * 0.5
            label = f"{secs:.1f}s"
            lines.append(
                f'  <text x="{cx:.2f}" y="{cy:.2f}" text-anchor="middle" dominant-baseline="middle" '
                f'class="lbl">{_escape_xml(label)}</text>'
            )

    # Draw actual detected room connections (including plain openings with no door blocks).
    conn_half = max(1.0, min(4.0, scale * 0.40))
    for conn in connections:
        cx = sx(float(conn["x"]) + 0.5)
        cz = sy(float(conn["z"]) + 0.5)
        axis = str(conn.get("axis", ""))
        kind = str(conn.get("kind", "open"))
        if axis == "x":
            x1, y1 = cx, cz - conn_half
            x2, y2 = cx, cz + conn_half
        elif axis == "z":
            x1, y1 = cx - conn_half, cz
            x2, y2 = cx + conn_half, cz
        else:
            x1, y1 = cx - conn_half * 0.8, cz - conn_half * 0.8
            x2, y2 = cx + conn_half * 0.8, cz + conn_half * 0.8
        color = "#9ee8ff" if kind == "open" else "#f0e5c8"
        conn_title = _escape_xml(
            f"connection {int(conn['a'])}<->{int(conn['b'])} ({kind}) @ "
            f"{int(float(conn['x']))},{int(conn['y'])},{int(float(conn['z']))}"
        )
        lines.append(
            f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{color}" stroke-opacity="0.78" stroke-width="2.0" stroke-linecap="round">'
            f"<title>{conn_title}</title></line>"
        )

    # Draw actual door blocks from world scan (some room connections are just openings, so no marker).
    door_half = max(1.2, min(4.2, scale * 0.42))
    for door in doors:
        dx = sx(float(door["x"]) + 0.5)
        dz = sy(float(door["z"]) + 0.5)
        d_type = str(door.get("type", "oak"))
        d_facing = str(door.get("facing", "unknown")).lower()
        color = "#d8c8a2" if d_type == "oak" else "#d7e3ef"
        stroke = "#4e3617" if d_type == "oak" else "#5d6f82"
        if d_facing in ("north", "south"):
            x1, y1 = dx - door_half, dz
            x2, y2 = dx + door_half, dz
        elif d_facing in ("east", "west"):
            x1, y1 = dx, dz - door_half
            x2, y2 = dx, dz + door_half
        else:
            x1, y1 = dx - door_half * 0.85, dz - door_half * 0.85
            x2, y2 = dx + door_half * 0.85, dz + door_half * 0.85
        lines.append(
            f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{color}" stroke-width="3.2" stroke-linecap="round">'
            f"<title>{d_type} door @ {int(float(door['x']))},{int(door['y'])},{int(float(door['z']))} facing {d_facing}</title></line>"
        )
        lines.append(
            f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="1.1" stroke-linecap="round" />'
        )

    # Draw stronghold chests.
    chest_half = max(1.3, min(3.8, scale * 0.35))
    for chest in chests:
        cx = sx(float(chest["x"]) + 0.5)
        cz = sy(float(chest["z"]) + 0.5)
        ctype = str(chest.get("type", "chest"))
        room_id = int(chest.get("room_id", -1) or -1)
        room_type = str(chest.get("room_type", "Unknown"))
        fill = "#d29a5f" if ctype == "chest" else "#b36adf"
        stroke = "#4e3116" if ctype == "chest" else "#43185c"
        chest_title = _escape_xml(
            f"{ctype} @ {int(float(chest['x']))},{int(chest['y'])},{int(float(chest['z']))} | room #{room_id} {room_type}"
        )
        lines.append(
            f'  <rect x="{(cx-chest_half):.2f}" y="{(cz-chest_half):.2f}" '
            f'width="{(2.0*chest_half):.2f}" height="{(2.0*chest_half):.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2">'
            f"<title>{chest_title}</title></rect>"
        )

    # Draw path
    path_segments_screen: list[list[tuple[float, float]]] = []
    if len(poly_points) >= 2:
        # Break polyline on unrealistic jumps (portal transition/outliers) so we don't draw to "narnia".
        jump_break_blocks = 80.0
        prev_world: tuple[float, float] | None = None
        current_segment: list[tuple[float, float]] = []
        segments: list[list[tuple[float, float]]] = []
        for point in draw_points:
            try:
                wx = float(point.get("x"))
                wz = float(point.get("z"))
            except Exception:
                continue
            if prev_world is not None:
                dx = wx - prev_world[0]
                dz = wz - prev_world[1]
                if (dx * dx + dz * dz) ** 0.5 > jump_break_blocks:
                    if len(current_segment) >= 2:
                        segments.append(current_segment)
                    current_segment = []
            current_segment.append((sx(wx), sy(wz)))
            prev_world = (wx, wz)
        if len(current_segment) >= 2:
            segments.append(current_segment)
        path_segments_screen = segments
        for segment in segments:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in segment)
            lines.append(
                f'  <polyline points="{pts}" fill="none" stroke="#ffffff" '
                f'stroke-opacity="0.75" stroke-width="2.2"/>'
            )
    marker_start: tuple[float, float] | None = None
    marker_end: tuple[float, float] | None = None
    if path_segments_screen:
        marker_start = path_segments_screen[0][0]
        marker_end = path_segments_screen[-1][-1]
    elif poly_points:
        marker_start = poly_points[0]
        marker_end = poly_points[-1]
    if marker_start is not None:
        sx0, sy0 = marker_start
        lines.append(
            f'  <circle cx="{sx0:.2f}" cy="{sy0:.2f}" r="5.5" fill="#3bff74" stroke="#072218" stroke-width="1.2">'
            "<title>Path start</title></circle>"
        )
    if marker_end is not None:
        sx1, sy1 = marker_end
        lines.append(
            f'  <circle cx="{sx1:.2f}" cy="{sy1:.2f}" r="5.5" fill="#ff5858" stroke="#2a0f0f" stroke-width="1.2">'
            "<title>Path end</title></circle>"
        )

    # Key room labels.
    key_labels: list[tuple[int, str]] = []
    if starter_id >= 0:
        starter_type = "Unknown"
        for room in rooms:
            if int(room["id"]) == starter_id:
                starter_type = str(room["type"])
                break
        starter_label = "Starter 5-Way" if starter_type == "FiveWayCrossing" else "Starter"
        key_labels.append((starter_id, starter_label))
    for room in rooms:
        room_id = int(room["id"])
        room_type = str(room["type"])
        if room_type == "PortalRoom":
            key_labels.append((room_id, "Portal"))
        elif room_type == "Library":
            key_labels.append((room_id, "Library"))

    emitted: set[tuple[int, str]] = set()
    for room_id, label in key_labels:
        key = (room_id, label)
        if key in emitted:
            continue
        emitted.add(key)
        target = None
        for room in rooms:
            if int(room["id"]) == room_id:
                target = room
                break
        if target is None:
            continue
        rx = min(sx(target["min_x"]), sx(target["max_x"]))
        ry = min(sy(target["max_z"]), sy(target["min_z"]))
        rw = max(1.0, abs(sx(target["max_x"]) - sx(target["min_x"])))
        tag_x = rx + 4.0
        tag_y = max(margin + header + 14.0, ry + 14.0)
        pad_x = 5.0
        tag_w = (len(label) * 7.0) + 2.0 * pad_x
        tag_h = 18.0
        lines.append(
            f'  <rect x="{tag_x:.2f}" y="{(tag_y-tag_h+3):.2f}" width="{tag_w:.2f}" height="{tag_h:.2f}" '
            f'rx="4" fill="#0b1826" fill-opacity="0.92" stroke="#8ecdf8" stroke-width="0.8"/>'
        )
        lines.append(
            f'  <text x="{(tag_x+pad_x):.2f}" y="{tag_y:.2f}" class="keyTag">{_escape_xml(label)}</text>'
        )

    # Subtle chunk grid overlay (16x16 blocks) on top layer.
    chunk_stroke = "#88b1d6"
    chunk_opacity = "0.16"
    chunk_dash = "1.5,5.0"
    x_chunk_start = int(min_x) // 16
    x_chunk_end = int(max_x) // 16
    z_chunk_start = int(min_z) // 16
    z_chunk_end = int(max_z) // 16
    for cx in range(x_chunk_start, x_chunk_end + 2):
        wx = float(cx * 16)
        lx = sx(wx)
        lines.append(
            f'  <line x1="{lx:.2f}" y1="{(margin+header):.2f}" x2="{lx:.2f}" y2="{(margin+header+map_h):.2f}" '
            f'stroke="{chunk_stroke}" stroke-opacity="{chunk_opacity}" stroke-width="0.9" '
            f'stroke-dasharray="{chunk_dash}" />'
        )
    for cz in range(z_chunk_start, z_chunk_end + 2):
        wz = float(cz * 16)
        ly = sy(wz)
        lines.append(
            f'  <line x1="{margin:.2f}" y1="{ly:.2f}" x2="{(margin+map_w):.2f}" y2="{ly:.2f}" '
            f'stroke="{chunk_stroke}" stroke-opacity="{chunk_opacity}" stroke-width="0.9" '
            f'stroke-dasharray="{chunk_dash}" />'
        )

    # Legend
    lx = margin
    ly = margin + 62.0
    lines.append(f'  <text x="{lx:.1f}" y="{ly:.1f}" class="legend">Time in room</text>')
    grad_w = 220.0
    grad_h = 14.0
    for i in range(0, 50):
        t0 = i / 50.0
        x = lx + 120.0 + grad_w * t0
        w = grad_w / 50.0 + 0.2
        c = _room_fill_for_seconds(t0 * max_seconds, max_seconds, True)
        lines.append(
            f'  <rect x="{x:.2f}" y="{ly-12:.2f}" width="{w:.2f}" height="{grad_h:.2f}" fill="{c}" stroke="none"/>'
        )
    lines.append(
        f'  <text x="{lx+120:.1f}" y="{ly+20:.1f}" class="legendSmall">0.0s</text>'
    )
    lines.append(
        f'  <text x="{lx+120+grad_w-10:.1f}" y="{ly+20:.1f}" class="legendSmall">{max_seconds:.1f}s</text>'
    )

    lines.append("</svg>")
    svg_text = "\n".join(lines) + "\n"
    output_svg.write_text(svg_text, encoding="utf-8")
    latest_svg = output_svg.parent / "latest.svg"
    if latest_svg.resolve() != output_svg.resolve():
        latest_svg.write_text(svg_text, encoding="utf-8")
    return output_svg


def main() -> int:
    parser = argparse.ArgumentParser(description="Render topdown stronghold map SVG from zdash_stronghold_map.json")
    parser.add_argument("json_path", help="Path to zdash_stronghold_map.json")
    parser.add_argument("--out", help="Output .svg path (default: same name with .svg)")
    parser.add_argument("--width", type=int, default=1800)
    parser.add_argument("--height", type=int, default=1200)
    args = parser.parse_args()

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"Map JSON not found: {json_path}")
        return 2
    out = Path(args.out) if args.out else None
    rendered = render_map_svg(json_path, out, width=int(args.width), height=int(args.height))
    print(f"Wrote topdown SVG: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
