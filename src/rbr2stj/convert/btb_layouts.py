"""A BTB stage and the same road run the other way as one package (STJ 0.4, spec 3.5).

BTB ships the two directions as two stage folders. Each holds the whole venue, and they differ in places: roadside
objects, a few ground triangles, a texture. They become one package in which

- what is **identical** in both is written once, in the common meshes;
- everything else goes in each direction's own meshes: the forward ones belong to the default layout, the reverse ones
  are `layout_only` and the `reverse` layout includes them.

So each selection is exactly its source stage and no value is changed. "Identical" means:
- a collision triangle: the same three corners as written (4 decimals), the same winding, and at every corner the same
  surfaces with the same weights;
- a visual placement: the same matrix, and a model with the same vertices, faces, UV sets, blend weights, material
  definitions, texture file contents and LOD distances (object numbers and `.x` file names are the exporter's and are
  not compared);
- a box collider: the same box and the same placement as written.

A folder is merged only when the evidence is there (see `check_same_road`); otherwise it stays a separate stage.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import CollisionMesh, Stage, SurfaceState
from . import Options
from .layouts import _TAG, NEAR_M

SIDES = ("common", "forward", "reverse")
_NUMBER_TAG = re.compile(r"[ _-]+(II|R)$")          # `Bareyo II`, `Te Parae Road R`
ROAD_M = 5.0                 # 99 % of the forward line within this of the other line: one road
ROAD_SHARE = 0.99
GROUND_SHARE = 0.5           # at least this share of the forward ground triangles identical in the other stage
_ZERO = 1e-4                 # a surface weight below this is no surface


# ── which folder ─────────────────────────────────────────────────────────────

def _strip_tags(name: str) -> str:
    return _TAG.sub(" ", _NUMBER_TAG.sub("", name))


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _strip_tags(name).lower())


def is_reverse_name(name: str) -> bool:
    return bool(_NUMBER_TAG.search(name) or _TAG.search(name))


def find_btb_reverse_folders(folder: Path) -> list[Path]:
    """Sibling BTB folders named like this stage plus a reverse tag (`II`, `R`, `Reverse`, `Rever`, `Rev`). The name only
    proposes a candidate; `check_same_road` decides."""
    from ..readers import btb
    folder = Path(folder).resolve()
    if is_reverse_name(folder.name) or not folder.parent.is_dir() or not _key(folder.name):
        return []
    key = _key(folder.name)
    return sorted(d for d in folder.parent.iterdir()
                  if d.is_dir() and d != folder and is_reverse_name(d.name) and _key(d.name) == key and btb.is_btb_folder(d))


# ── identity ─────────────────────────────────────────────────────────────────

def _rotate_min(corners: tuple) -> tuple:
    """The cyclic rotation that sorts first: the same triangle with the same winding gives the same key."""
    return min(corners[r:] + corners[:r] for r in range(3))


def triangle_keys(mesh: CollisionMesh) -> list[tuple]:
    st = mesh.states[0]
    pts = [tuple(round(c, 4) for c in p) for p in mesh.points]
    keys = []
    for i, face in enumerate(mesh.faces):
        corners = []
        for k, v in enumerate(face):
            wa, wb = st.weights[3 * i + k]
            w: dict = {}
            for layer, x in zip(st.layers[i], (wa, wb, 1.0 - wa - wb)):
                if x > _ZERO:
                    sid = st.palette[layer]
                    w[sid] = w.get(sid, 0.0) + x
            corners.append((pts[v], tuple(sorted((s, round(x, 4)) for s, x in w.items()))))
        keys.append(_rotate_min(tuple(corners)))
    return keys


def sub_mesh(mesh: CollisionMesh, faces: list[int]) -> CollisionMesh | None:
    """The chosen faces of a collision mesh, with only the points they use."""
    if not faces:
        return None
    index: dict[int, int] = {}
    points, out = [], []
    for i in faces:
        tri = []
        for v in mesh.faces[i]:
            if v not in index:
                index[v] = len(points)
                points.append(mesh.points[v])
            tri.append(index[v])
        out.append(tuple(tri))
    states = [SurfaceState(s.key, s.wear, s.wetness, s.palette, [s.layers[i] for i in faces],
                           [s.weights[3 * i + k] for i in faces for k in range(3)], s.merged) for s in mesh.states]
    return CollisionMesh(points, out, states)


def match(a: list, b: list) -> tuple[list[int], list[int], list[int]]:
    """Multiset match of two key lists: (indices of `a` also in `b`, the rest of `a`, the rest of `b`)."""
    free = Counter(b)
    common, only_a = [], []
    taken: Counter = Counter()
    for i, k in enumerate(a):
        if free[k] > 0:
            free[k] -= 1
            taken[k] += 1
            common.append(i)
        else:
            only_a.append(i)
    only_b = []
    for j, k in enumerate(b):
        if taken[k] > 0:
            taken[k] -= 1
        else:
            only_b.append(j)
    return common, only_a, only_b


class ModelKeys:
    """The identity of a visual object's model in one stage: geometry, materials and the bytes of their textures."""

    def __init__(self, stage: Stage):
        from .. import textures
        from ..readers.stage_files import in_dir
        self.track = stage.btb_track
        source = stage.texture_source
        if source is None:                           # --no-textures: the files still decide what is identical
            tz = in_dir(self.track.folder, "Textures.zip")
            source = textures.TextureSource([tz] if tz else [], [], [])
        self.source = source
        self._tex: dict[str, str] = {}
        self._mat: dict[str, tuple] = {}
        self._mesh: dict[int, str] = {}

    def _texture(self, raw: str) -> str:
        if raw not in self._tex:
            e = self.source.find(raw)
            self._tex[raw] = hashlib.sha256(self.source.read(e)).hexdigest() if e else "missing:" + _base_name(raw)
        return self._tex[raw]

    def _material(self, name: str) -> tuple:
        key = name.lower()
        if key not in self._mat:
            sec = self.track.materials.get(key)
            if sec is None:
                self._mat[key] = (key, None)
            else:
                extras = []
                for k, v in sorted(sec.extras.items()):
                    if k in ("SpecularPower", "SpecularColor"):
                        extras.append((k, repr(v)))
                    else:
                        extras.append((k, _base_name(v), self._texture(v)))
                diffuse = (_base_name(sec.diffuse), self._texture(sec.diffuse)) if sec.diffuse else None
                self._mat[key] = (key, sec.effect, sec.technique, diffuse, tuple(extras))
        return self._mat[key]

    def key(self, item: dict) -> tuple:
        mesh, o = item["mesh"], item["object"]
        if id(mesh) not in self._mesh:
            data = repr((mesh.vertices, mesh.triangles, mesh.uvs, mesh.extra_uvs, mesh.blend, mesh.face_materials,
                         [self._material(n) for n in mesh.material_names]))
            self._mesh[id(mesh)] = hashlib.sha256(data.encode("utf-8")).hexdigest()
        return (self._mesh[id(mesh)], o.lod_in, o.lod_out)


def _base_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def split_visual(a: Stage, b: Stage) -> dict[str, list[dict]]:
    """Visual items per side. A placement list is cut out of its object; the object keeps being instanced (`instancer`)
    when it was in its source stage, so a part with one placement left is still written as an instance set."""
    ka, kb = ModelKeys(a), ModelKeys(b)

    def keys(stage, model):
        out, where = [], []
        for i, item in enumerate(stage.visual):
            mk = model.key(item)
            for j, m in enumerate(item["matrices"]):
                out.append((mk, tuple(m)))
                where.append((i, j))
        return out, where

    xa, wa = keys(a, ka)
    xb, wb = keys(b, kb)
    common, only_a, only_b = match(xa, xb)

    def items(stage, where, chosen):
        per: dict[int, list[int]] = {}
        for n in chosen:
            i, j = where[n]
            per.setdefault(i, []).append(j)
        out = []
        for i, js in per.items():
            item = stage.visual[i]
            out.append({**item, "matrices": [item["matrices"][j] for j in js], "instancer": len(item["matrices"]) > 1})
        return out

    return {"common": items(a, wa, common), "forward": items(a, wa, only_a), "reverse": items(b, wb, only_b)}


def split_boxes(a: Stage, b: Stage):
    """(prototype Props without placements, instance sets [(prop index, side, rows, placements_raw)])."""
    def shape_key(p):
        return (p.kind, p.material, p.dynamic, tuple(tuple(round(c, 4) for c in v) for v in p.vertices), tuple(p.faces))

    def row_key(row):
        x, y, z, h, s = row
        return (round(x, 3), round(y, 3), round(z, 3), round(h, 4), round(s, 4))

    protos: list = []
    index: dict = {}

    def flatten(stage):
        keys, where = [], []
        for p in stage.props:
            sk = shape_key(p)
            if sk not in index:
                index[sk] = len(protos)
                protos.append(p)
            for row in p.transforms:
                keys.append((index[sk], row_key(row)))
                where.append((index[sk], row, p))
        return keys, where

    xa, wa = flatten(a)
    xb, wb = flatten(b)
    common, only_a, only_b = match(xa, xb)
    sets = []
    for side, where, chosen in (("common", wa, common), ("forward", wa, only_a), ("reverse", wb, only_b)):
        per: dict = {}
        for n in chosen:
            pi, row, source = where[n]
            per.setdefault((pi, id(source)), (pi, [], source.placements_raw))[1].append(row)
        sets += [(pi, side, rows, raw) for pi, rows, raw in per.values()]
    return protos, sets


@dataclass
class Merge:
    """What the package writer needs on top of the forward stage."""
    reverse: Stage
    collision: dict[str, CollisionMesh | None] = field(default_factory=dict)
    visual: dict[str, list] = field(default_factory=dict)
    box_protos: list = field(default_factory=list)
    box_sets: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def check_same_road(a: Stage, b: Stage, ground: tuple | None) -> str | None:
    """None when `b` is `a`'s road run the other way, otherwise why not. `ground` is `match()` of the two collision
    meshes (None when either stage has none)."""
    import math
    fp, rp = a.line.points, b.line.points
    if not fp or not rp:
        return "no driveline"
    if math.dist(rp[0], fp[-1]) > NEAR_M or math.dist(rp[-1], fp[0]) > NEAR_M:
        return (f"its line does not run the forward line backwards (starts {math.dist(rp[0], fp[-1]):.0f} m from the "
                f"forward finish, ends {math.dist(rp[-1], fp[0]):.0f} m from its start)")
    grid: dict = {}
    for p, q in zip(rp, rp[1:]):
        for cx in range(int(min(p[0], q[0]) // 50) - 1, int(max(p[0], q[0]) // 50) + 2):
            for cy in range(int(min(p[1], q[1]) // 50) - 1, int(max(p[1], q[1]) // 50) + 2):
                grid.setdefault((cx, cy), []).append((p, q))

    def near(p):
        for u, v in grid.get((int(p[0] // 50), int(p[1] // 50)), ()):
            dx, dy = v[0] - u[0], v[1] - u[1]
            t = max(0.0, min(1.0, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / ((dx * dx + dy * dy) or 1.0)))
            if math.hypot(p[0] - u[0] - t * dx, p[1] - u[1] - t * dy) <= ROAD_M:
                return True
        return False

    share = sum(near(p) for p in fp) / len(fp)
    if share < ROAD_SHARE:
        return f"only {share:.0%} of the forward line lies within {ROAD_M:.0f} m of its line: another road"
    if ground is None:
        return "one of the two stages has no ground mesh to compare"
    same = len(ground[0]) / (len(ground[0]) + len(ground[1]))
    if same < GROUND_SHARE:
        return f"only {same:.0%} of the ground triangles are identical: the ground was rebuilt"
    return None


def merge_reverse(forward: Stage, folder: Path, opt: Options):
    """Convert `folder` in the forward stage's coordinates. Returns (Merge, None), or (None, why it stays separate).
    On success `forward.layouts` holds the `reverse` entry (without `include_meshes`: the writer knows the mesh ids)."""
    from ..errors import Rbr2StjError
    from ..writers.stj_doc import layout_entry
    from .btb import convert_btb
    folder = Path(folder)
    try:
        rev = convert_btb(folder, opt, shift=forward.shift)
    except Rbr2StjError as e:
        return None, f"{folder.name}: {e}"
    ground = None
    if forward.collision is not None and rev.collision is not None:
        ground = match(triangle_keys(forward.collision), triangle_keys(rev.collision))
    why = check_same_road(forward, rev, ground)
    if why:
        return None, f"{folder.name}: {why}"
    m = Merge(rev)
    common, only_a, only_b = ground
    m.collision = {"common": sub_mesh(forward.collision, common), "forward": sub_mesh(forward.collision, only_a),
                   "reverse": sub_mesh(rev.collision, only_b)}
    m.visual = split_visual(forward, rev)
    m.box_protos, m.box_sets = split_boxes(forward, rev)

    def count(items):
        return sum(len(i["matrices"]) for i in items)

    m.stats = {
        "collision_triangles": {"common": len(common), "forward": len(only_a), "reverse": len(only_b)},
        "visual_placements": {s: count(m.visual[s]) for s in SIDES},
        "box_placements": {s: sum(len(r) for _p, side, r, _raw in m.box_sets if side == s) for s in SIDES},
    }
    forward.diagnostics["layout_merge"] = m.stats
    rx = rev.x_rbr
    x = {"source": rev.source,
         **{k: rx.get(k) for k in ("track", "chainage_offset", "physics", "splashscreen", "date", "version",
                                   "sun_direction", "tangents_raw", "notes_skipped") if rx.get(k) is not None},
         "objects": rev.x_btb.get("objects", [])}
    own_materials = {k: v for k, v in rev.x_btb.get("materials", {}).items() if forward.x_btb.get("materials", {}).get(k) != v}
    if own_materials:
        x["materials"] = own_materials
    if rev.info.author and rev.info.author != forward.info.author:
        x["author"] = rev.info.author
    if rev.info.comment and rev.info.comment != forward.info.comment:
        x["comment"] = rev.info.comment
    if rev.info.surface != forward.info.surface:
        x["primary_surface"] = rev.info.surface
    forward.layouts = [layout_entry("reverse", rev.info.name, rev, False, x)]
    return m, None
