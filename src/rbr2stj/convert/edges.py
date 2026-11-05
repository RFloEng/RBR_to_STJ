"""Fences (`.fnc`) and the brake wall (`.col`) -> STJ `edges[]`, placed on the reference line by projection.

Fence type table (what each `.fnc` fence type is): reRBR crates/rbr-render/src/fences/cloth/mod.rs `TYPES`
(`g_fenceTypes`, 15 rows): row `fence_type` has a height (0.074 m = a tape/ribbon, 0.9 m = a net/fence) and a flag
whose non-zero value makes the spans tear. The file's fence type indexes that table.

Brake wall: reRBR notes/formats.md "Collision": (inner, outer) point pairs along the road with per-item flags
(bit 14 sensor only, bit 15 respawn). Plain items drag. The segment-index reading of an item is inferred.
"""

from __future__ import annotations

import math

# (height m, tears) for fence type index 0..14 (`TYPES`: id = index + 1)
FENCE_TYPES = [(0.074, 1), (0.074, 1), (0.9, 0), (0.9, 0), (0.074, 1), (0.9, 1), (0.074, 1), (0.9, 1), (0.074, 1),
               (0.9, 1), (0.074, 1), (0.9, 1), (0.074, 0), (0.074, 0), (0.074, 0)]

# placeholder drag coefficient for the brake wall (no number in the stage files; physics lives in the exe/NGP)
DRAG_ESTIMATE = 0.5


def fence_style(fence_type: int) -> tuple[str, bool, float]:
    """(STJ style, tearable, height m) of a fence type; out-of-range types clamp to the last row (as the game does)."""
    height, tears = FENCE_TYPES[min(fence_type, len(FENCE_TYPES) - 1)]
    return ("tape" if height < 0.2 else "fence"), bool(tears), height


def _runs(items):
    """Split [(s, t, payload)] into runs of the same side (sign of t)."""
    runs, cur = [], []
    for it in items:
        if cur and (it[1] >= 0) != (cur[-1][1] >= 0):
            runs.append(cur)
            cur = []
        cur.append(it)
    if cur:
        runs.append(cur)
    return runs


def fence_edges(fences, line, first_id: int = 0):
    edges = []
    for gi, f in enumerate(fences):
        style, tears, height = fence_style(f.fence_type)
        proj = [(*line.project(p[0], p[1]), p) for p in f.posts]
        for ri, run in enumerate(_runs([(s, t, p) for s, t, p in proj])):
            ss = [r[0] for r in run]
            if len(run) < 2 or max(ss) - min(ss) < 0.01:
                continue
            left = run[0][1] >= 0
            ts = [r[1] for r in run]
            inner = min(ts) if left else max(ts)
            edges.append({
                "id": f"fence_{gi}_{ri}", "side": "left" if left else "right",
                "s_start": round(min(ss), 3), "s_end": round(max(ss), 3), "kind": "barrier", "style": style,
                "t": round(inner, 3), "height": height, "tearable": tears,
                "x_rbr": {"fence_type": f.fence_type, "post_type": f.post_type, "material": f.material,
                          "rail_material": f.rail_material,
                          "posts": [[round(c, 3) for c in r[2]] for r in run]}})
    return edges


def brake_wall_edges(wall, line):
    """wall: readers.col.BrakeWall (points already in STJ coordinates). One edge per run of equal-behaviour items."""
    pts = wall.points
    segs = []
    for it in sorted(wall.items, key=lambda i: i.index):
        k = it.index
        if 2 * k + 3 >= len(pts):
            continue
        inner = [pts[2 * k], pts[2 * (k + 1)]]
        outer = [pts[2 * k + 1], pts[2 * (k + 1) + 1]]
        pi = [line.project(*p) for p in inner]
        po = [line.project(*p) for p in outer]
        beh = "respawn" if it.respawn else ("sensor" if it.sensor else "drag")
        t_in = sum(p[1] for p in pi) / 2
        t_out = sum(p[1] for p in po) / 2
        segs.append((beh, k, min(pi[0][0], pi[1][0]), max(pi[0][0], pi[1][0]), t_in, t_out))
    edges = []
    cur = None
    for beh, k, s0, s1, t_in, t_out in segs:
        same_side = cur and (cur["t_in"] >= 0) == (t_in >= 0)
        if cur and cur["beh"] == beh and cur["last"] + 1 == k and same_side:
            cur["s0"], cur["s1"] = min(cur["s0"], s0), max(cur["s1"], s1)
            cur["t_in"] = min(cur["t_in"], t_in) if t_in >= 0 else max(cur["t_in"], t_in)
            cur["t_out"] = max(cur["t_out"], t_out) if t_in >= 0 else min(cur["t_out"], t_out)
            cur["last"] = k
            cur["items"].append(k)
        else:
            if cur:
                edges.append(cur)
            cur = {"beh": beh, "last": k, "s0": s0, "s1": s1, "t_in": t_in, "t_out": t_out, "items": [k]}
    if cur:
        edges.append(cur)
    out = []
    for i, c in enumerate(edges):
        if c["s1"] - c["s0"] < 0.01:
            continue
        e = {"id": f"brake_wall_{i}", "side": "left" if c["t_in"] >= 0 else "right", "s_start": round(c["s0"], 3),
             "s_end": round(c["s1"], 3), "kind": "invisible", "behaviour": c["beh"], "t": round(c["t_in"], 3),
             "width": round(abs(c["t_out"] - c["t_in"]), 3),
             "x_rbr": {"items": c["items"]}}
        if c["beh"] == "drag":
            e["drag_coefficient"] = DRAG_ESTIMATE
            e["_est"] = True
            e["_note"] = "placeholder: the stage files carry no drag number (the penalty is computed by the game)"
        out.append(e)
    return out


def point_in_polygon(poly, x, y):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi:
            inside = not inside
        j = i
    return inside


def polygon_crosses_line(poly, line, step: float = 1.0) -> bool:
    """True when the reference line passes through the polygon (a ford: water across the road)."""
    s = 0.0
    while s <= line.length:
        x, y, _z, _h = line.frame(s)
        if point_in_polygon(poly, x, y):
            return True
        s += step
    return False
