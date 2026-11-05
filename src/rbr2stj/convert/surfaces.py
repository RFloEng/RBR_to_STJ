"""Surface blending helpers shared by the classic (`.col` + `.mat`) and BTB (`mat.ini` + UV) converters.

RBR resolves the surface under a wheel per contact point from two sources and blends them (reRBR notes/formats.md
"Collision": two `.mat` run indices per triangle, per-vertex cell coordinates, attr A / 31 = blend weight from
surface 0 towards surface 1). STJ expresses the same with a surface map in the `ground` `vertex_top3` encoding:
per face the three strongest surfaces (palette indices) and per face corner their weights (spec §6.3, §8.7).

We sample the `.mat` cell lookup at the three triangle corners. A surface change INSIDE a triangle (the cell lookup
interpolates the coordinate across the face) is therefore reduced to its corner values; this is recorded in `lost`.
"""

from __future__ import annotations

from collections import defaultdict


def top3(corners: list[dict[int, float]]):
    """corners: for each of the 3 face corners {surface id: weight} (weights sum to 1).

    Returns (ids, weights): the three strongest ids over the face (padded with the first id, weight 0) and per
    corner (wa, wb) with wc = 1 - wa - wb."""
    total: dict[int, float] = defaultdict(float)
    for c in corners:
        for sid, w in c.items():
            total[sid] += w
    order = sorted(total, key=lambda k: (-total[k], k))[:3]
    ids = list(order)
    while len(ids) < 3:
        ids.append(ids[0])
    real = len(order)
    out = []
    for c in corners:
        w = [c.get(ids[i], 0.0) if i < real else 0.0 for i in range(3)]
        s = sum(w)
        if s <= 1e-9:
            w = [1.0, 0.0, 0.0]
        else:
            w = [x / s for x in w]
        wa, wb = round(w[0], 4), round(w[1], 4)
        if wa + wb > 1.0:
            wb = round(1.0 - wa, 4)
        out.append((wa, wb))
    return tuple(ids), out


def corner_weights(s0: int, s1: int, w: float) -> dict[int, float]:
    """Blend surface s0 -> s1 by w (0..1) into {id: weight}."""
    if s0 == s1 or w <= 0.0:
        return {s0: 1.0}
    if w >= 1.0:
        return {s1: 1.0}
    return {s0: 1.0 - w, s1: w}


def pack_state(per_face):
    """per_face: list of (ids3, corner weights) -> (palette, layers per face, weights per corner)."""
    palette = sorted({i for ids, _w in per_face for i in ids})
    index = {sid: k for k, sid in enumerate(palette)}
    layers = [tuple(index[i] for i in ids) for ids, _w in per_face]
    weights = [w for _ids, ws in per_face for w in ws]
    return palette, layers, weights
