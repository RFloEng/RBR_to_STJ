"""TEST-ONLY writers for every binary stage format, plus builders of a synthetic classic stage and a synthetic BTB
folder. Nothing here reads game data: every layout is the one documented in the corresponding reader's docstring
(which cites the reRBR format notes), so a write -> read round trip proves reader and writer agree on the notes,
not that the notes are right (real files are tested only when RBR_DATA / RBR_RSF is set).

The synthetic stage ("Test Stage"): a 7 m wide gravel road in the STJ/.trk frame (Z up, right-handed) that runs
200 m east, bends LEFT (radius 60, 90 deg), runs 140 m north, bends RIGHT (radius 60, 90 deg) and runs 200 m east.
"""

from __future__ import annotations

import math
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from rbr2stj.readers import xfile, xform
from rbr2stj.readers.chunks import write_chunk

# ── low-level helpers ────────────────────────────────────────────────────────

def p32(*v: int) -> bytes:
    return struct.pack(f"<{len(v)}I", *v)


def pf(*v: float) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def cstr(s: str) -> bytes:
    return s.encode() + b"\0"


def pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


# ── .trk ─────────────────────────────────────────────────────────────────────

def write_trk(driveline, meshes=()) -> bytes:
    """driveline: [(pos, tangent, distance)]; meshes: dicts (see build_classic_stage)."""
    dl = p32(len(driveline)) + b"".join(pf(*p, *t, d) + struct.pack("<HH", 0, 0) for p, t, d in driveline)
    out = write_chunk((3, 0x14), dl)
    m = p32(len(meshes))
    for me in meshes:
        m += cstr(me["name"]) + p32(me["kind"]) + bytes([me.get("material", 0), me.get("dynamic", 0)])
        shape = me.get("shape")
        if shape is None:
            m += p32(0)
        elif shape[0] == "box":
            m += p32(1) + pf(*shape[1], *shape[2])
        else:
            m += p32(2) + pf(*shape[1], shape[2])
        m += p32(len(me["vertices"])) + b"".join(pf(*v) for v in me["vertices"])
        m += p32(len(me["faces"])) + b"".join(bytes([f[0]]) + p32(f[1], f[2], f[3]) for f in me["faces"])
        m += p32(len(me["placements"]))
        for pl in me["placements"]:
            m += p32(pl.get("key", 0)) + pf(*pl["position"], *pl["scale"], *pl["quat"])
    return out + write_chunk((3, 0x16), m)


# ── .dls ─────────────────────────────────────────────────────────────────────

def write_dls(keys, triggers=(), sphere=(), named=(), extra_sets=()) -> bytes:
    """keys: [(tag, flags, distance)] of the Driveline set; triggers: [(object id, distance, lead)];
    named: [(name, object id)]; extra_sets: names of other (empty) animation sets written first."""
    names = ["Driveline"] + list(extra_sets)
    name_block = b""
    name_off = {}
    for n in names + [n for n, _ in named]:
        if n not in name_off:
            name_off[n] = len(name_block)
            name_block += cstr(n)
    sets = list(extra_sets) + ["Driveline"]            # Driveline deliberately not first

    body = bytearray()
    header_len = 56
    cur = header_len

    def add(b: bytes) -> int:
        nonlocal cur
        off = cur
        body.extend(b)
        cur += len(b)
        return off

    t0 = add(p32(len(sets)) + b"\0" * (8 * len(sets)))            # patched below
    t1 = add(p32(0))
    t2 = add(p32(0))
    t3 = add(p32(0))
    t4 = add(p32(0))
    t5 = add(p32(0))
    t6 = add(p32(len(sphere)) + b"".join(pf(*s) for s in sphere))
    t7 = add(p32(len(name_block)) + name_block + b"\0" * (-len(name_block) % 4))
    t9 = add(p32(len(named)) + b"".join(p32(obj, name_off[n]) for n, obj in named))

    records = []
    for sname in sets:
        # trigger data: one {object, kind} per animation channel
        tdata = [(obj, 0) for obj, _d, _l in triggers] if sname == "Driveline" else []
        chans = [(i, d, l) for i, (_o, d, l) in enumerate(triggers)] if sname == "Driveline" else []
        ks = list(keys) if sname == "Driveline" else []
        a_td = add(b"".join(p32(*r) for r in tdata)) if tdata else 0
        a_ch = add(b"".join(p32(i) + pf(d, l) for i, d, l in chans)) if chans else 0
        a_ks = add(b"".join(p32(t, f) + pf(d) for t, f, d in ks)) if ks else 0
        replay = add(p32(0, 0, 0, 0, 0, 0, 0))
        w = [name_off[sname], len(tdata), 0, len(chans), 0, 0, len(ks), 0, 0, a_td, 0, a_ch, 0, 0, a_ks, 0, replay]
        records.append(add(p32(*w)))
    # patch animation-set table
    tbl = p32(len(sets)) + b"".join(p32(r, 0) for r in records)
    body[t0 - header_len:t0 - header_len + len(tbl)] = tbl
    head = b"MINAATAD" + p32(18, 0, t0, t1, t2, t3, t4, t5, t6, t7, 0xFFFFFFFF, t9)
    assert len(head) == header_len
    return head + bytes(body)


# ── .mat ─────────────────────────────────────────────────────────────────────

def write_mat(entries, tail: bytes = b"") -> bytes:
    """entries: [(name, [cells_bytes(256) per run])]"""
    out = p32(len(entries))
    for name, runs in entries:
        out += cstr(name) + p32(len(runs))
        for cells in runs:
            assert len(cells) == 256
            out += p32(16, 16) + bytes(cells)
    return out + tail


# ── .fnc ─────────────────────────────────────────────────────────────────────

def write_fnc(groups, textures=()) -> bytes:
    """groups: [dict(fence_type, post_type, material, rail_material, posts=[(pos, colour)])]"""
    out = p32(2, len(groups))
    for g in groups:
        posts = g["posts"]
        xs = [pos for pos, _c in posts] or [(0, 0, 0)]
        centre = tuple(sum(c[i] for c in xs) / len(xs) for i in range(3)) if posts else (0, 0, 0)
        out += p32(len(posts), g["fence_type"], g["post_type"], g["material"], g["rail_material"]) + pf(*centre, 1, 1, 1)
        for pos, colour in posts:
            out += pf(*pos, *pos, 0.5, 0.5, 0.5) + p32(colour)
    if groups:
        out += p32(len(textures))
        if textures:
            out += p32(32) + b"".join(t.encode().ljust(32, b"\0") for t in textures)
    return out


# ── .lbs ─────────────────────────────────────────────────────────────────────

LBS_STRIDES = (0x10, 0x18, 0x30, 0x24, 0x3C, 0x20, 0x38, 0x2C, 0x44)
LBS_LAYOUT = {0: (12, None), 5: (12, 16), 7: (12, 16), 6: (24, 28), 8: (24, 28)}


def write_lbs_geometry(blocks) -> bytes:
    """blocks: [dict(views={slot: dict(indices=[...], vertices=[(pos, colour, uv)])}, chunks=[dict(slot, start, tris,
    textures, list)])]. Test twin of readers.lbs._geometry."""
    out = p32(len(blocks), 0)
    for b in blocks:
        for slot in range(9):
            v = b["views"].get(slot)
            if v is None:
                out += p32(0, 0)
                continue
            stride = LBS_STRIDES[slot]
            out += p32(len(v["indices"])) + struct.pack(f"<{len(v['indices'])}H", *v["indices"]) + p32(len(v["vertices"]))
            for pos, colour, uv in v["vertices"]:
                rec = bytearray(stride)
                rec[0:12] = pf(*pos)
                col_off, uv_off = LBS_LAYOUT.get(slot, (12, None))
                rec[col_off:col_off + 4] = p32(colour)
                if uv_off is not None and uv is not None:
                    rec[uv_off:uv_off + 8] = pf(*uv)
                out += bytes(rec)
        out += p32(len(b["chunks"]))
        for c in b["chunks"]:
            rec = bytearray(0x70)
            rec[0:4] = p32(c["slot"])
            rec[0x0C:0x14] = p32(c["start"], c["tris"])
            rec[0x34] = 1
            rec[0x44:0x48] = p32(len(c["textures"]))
            for i, t in enumerate(c["textures"]):
                rec[0x48 + 4 * i:0x4C + 4 * i] = p32(t)
            rec[0x6C] = c["list"]
            out += bytes(rec)
        out += pf(20, 0, 20, 20, 5, 20)
    return write_chunk((1, 0), out)


def write_lbs(start=None, anim_objects=(), geometry=None) -> bytes:
    out = write_lbs_geometry(geometry) if geometry else b""
    if start is not None:
        out += write_chunk((2, 11), pf(*start.get("matrix", [0.0] * 16), *start["position"], *start["rotation"]))
    if anim_objects:
        p = p32(len(anim_objects))
        for a in anim_objects:
            p += cstr(a["name"]) + p32(*a["ids"]) + cstr(a["scripts"][0]) + cstr(a["scripts"][1])
            p += b"".join(pf(*v) for v in a.get("vectors", [(0, 0, 0)] * 5)) + p32(a.get("colour", 0xFFFFFFFF))
        out += write_chunk((2, 14), p)
    return out


# ── .col ─────────────────────────────────────────────────────────────────────

def _tri_bytes(t) -> bytes:
    """t: dict v(3), attr_a(3 values 0..31), attr_b(3), mat(2), nib(2 x 3)."""
    aa = sum(((t["attr_a"][i] & 31) << (5 * i)) for i in range(3)) | (0x8000 if t.get("flag_a") else 0)
    ab = sum(((t["attr_b"][i] & 31) << (5 * i)) for i in range(3))
    return struct.pack("<3HHH2B6B", *t["v"], aa, ab, *t["mat"], *t["nib"][0], *t["nib"][1])


def _cell_blob(vertices, triangles, split: bool, graft: int) -> bytes:
    """One cell: header (0x14), vertices, tree. `split` makes the root an inner node over two leaves."""
    head_len = 0x14
    vbytes = b"".join(pf(*v) for v in vertices)
    tree_rel = head_len + len(vbytes)
    node = lambda flags, ptr: pf(0, 0, 0, 1, 1, 1) + p32(flags, ptr)  # noqa: E731
    if not split:
        arr = b"".join(_tri_bytes(t) for t in triangles)
        tree = node(len(triangles), 32) + pad4(arr)
    else:
        mid = len(triangles) // 2
        a, b = triangles[:mid], triangles[mid:]
        arr_a, arr_b = pad4(b"".join(_tri_bytes(t) for t in a)), pad4(b"".join(_tri_bytes(t) for t in b))
        pair_at = 32                       # node 0 is 32 bytes, the child pair follows
        arrs_at = pair_at + 64
        tree = (node(0, pair_at) + node(len(a), arrs_at) + node(len(b), arrs_at + len(arr_a)) + arr_a + arr_b)
    return struct.pack("<BBHIIII", 1, 1, 0, graft, len(vertices), head_len, tree_rel) + vbytes + tree


def write_col(cells, puddles=(), deep=(), wall=None) -> bytes:
    """cells: [dict(vertices, triangles, split=False, graft=word)]; puddles/deep: [4 corners (x, y, z)];
    wall: dict(points=[(x, y)], items=[(index, sensor, respawn)])."""
    # root: 0x28 header, wall def, mesh A, mesh B, root tree (inner node + two placeholders)
    root_off = 16
    wall_blob = b""
    if wall:
        pts = b"".join(pf(*p) for p in wall["points"])
        items = pad4(b"".join(struct.pack("<H", (i & 0x3FFF) | (0x4000 if s else 0) | (0x8000 if r else 0))
                              for i, s, r in wall["items"]))
        # layout: [16-byte def][points][root node 0x18][items]
        points_rel = 16
        root_rel = points_rel + len(pts)
        items_rel = 0x18   # relative to the tree root (as in real files)
        node = pf(0, 0, 100, 100) + p32(len(wall["items"]), items_rel)
        wall_blob = p32(16 + len(pts) + 0x18 + len(items), len(wall["points"]), points_rel, root_rel) + pts + node + items

    def quads(qs):
        return b"".join(b"".join(pf(*p, 0.0) for p in q) for q in qs)

    a_blob, b_blob = quads(puddles), quads(deep)
    ph = 0x00200000
    pnode = lambda flags, ptr: pf(0, 0, 0, 1, 1, 1) + p32(flags, ptr)  # noqa: E731
    root_tree = pnode(0, 32) + pnode(ph, 0) + pnode(ph, 0)
    off = 0x28
    wall_rel = off if wall_blob else 0
    off += len(wall_blob)
    a_rel = off
    off += len(a_blob)
    b_rel = off
    off += len(b_blob)
    tree_rel = off
    off += len(root_tree)
    root = (struct.pack("<BBHIII", 4, 3, 0, 0, 0, 0) + p32(tree_rel, wall_rel, len(puddles), a_rel, len(deep), b_rel))
    assert len(root) == 0x28
    root_block = root + wall_blob + a_blob + b_blob + root_tree
    table_off = root_off + len(root_block)
    cell_blobs = []
    pos = table_off + 4 * len(cells)
    offsets = []
    for c in cells:
        blob = _cell_blob(c["vertices"], c["triangles"], c.get("split", False), c["graft"])
        offsets.append(pos)
        cell_blobs.append(blob)
        pos += len(blob)
    header = p32(0x5237434F, root_off, len(cells), table_off)
    return header + root_block + p32(*offsets) + b"".join(cell_blobs)


# lbs geometry (Y up): one 40 x 40 ground quad at the origin in slot 5 (near + far copy) and an undecoded slot 1 view
LBS_STAGE_GEOMETRY = dict(
    views={5: dict(indices=[0, 2, 1, 0, 3, 2],
                   vertices=[((0.0, 0.0, 0.0), 0xFF102030, (0.0, 0.0)), ((40.0, 0.0, 0.0), 0xFF405060, (1.0, 0.0)),
                             ((40.0, 0.0, 40.0), 0xFF708090, (1.0, 1.0)), ((0.0, 0.0, 40.0), 0xFFA0B0C0, (0.0, 1.0))]),
           1: dict(indices=[0, 1, 2], vertices=[((0.0, 0.0, 0.0), 0, None)] * 3)},
    chunks=[dict(slot=5, start=0, tris=2, textures=[0, 1], list=2),
            dict(slot=5, start=0, tris=2, textures=[0], list=3),
            dict(slot=1, start=0, tris=1, textures=[0], list=2)])

# ── the synthetic classic stage ──────────────────────────────────────────────

ROAD_HALF = 3.5
VERGE = 6.0


def centreline(step: float = 20.0, z_slope: float = 0.03):
    """Points (x, y, z) and the arc-length chainage of the test road; also returns the s of the bends."""
    pts: list[tuple[float, float]] = []
    x = 0.0
    while x < 200 - 1e-9:
        pts.append((x, 0.0))
        x += step
    r = 60.0
    for k in range(0, 7):                                     # left bend, centre (200, 60): theta -90..0
        th = math.radians(-90 + 15 * k)
        pts.append((200 + r * math.cos(th), 60 + r * math.sin(th)))
    y = 60 + step
    while y < 200 - 1e-9:
        pts.append((260.0, y))
        y += step
    for k in range(0, 7):                                     # right bend, centre (320, 200): theta 180..90
        th = math.radians(180 - 15 * k)
        pts.append((320 + r * math.cos(th), 200 + r * math.sin(th)))
    x = 320 + step
    while x <= 520 + 1e-9:
        pts.append((x, 260.0))
        x += step
    z = [z_slope * (i * 10.0) for i in range(len(pts))]       # gentle climb
    return [(px, py, pz) for (px, py), pz in zip(pts, z)]


def tangents(points):
    """Catmull-Rom style dP/du per point (about one chord long): the game evaluates a Hermite curve with them."""
    n = len(points)
    out = []
    for i in range(n):
        a, b = points[max(i - 1, 0)], points[min(i + 1, n - 1)]
        k = 2.0 if 0 < i < n - 1 else 1.0
        out.append(tuple((b[j] - a[j]) / k for j in range(3)))
    return out


def stored_distance(points, offset: float = 0.0):
    d = [offset]
    for a, b in zip(points, points[1:]):
        d.append(d[-1] + math.dist(a, b))
    return d


# markers of the synthetic stage (distances along the stored driveline distance)
MARKERS = {"start": 30.0, "splits": [240.0, 480.0], "finish": 690.0, "end": 705.0}
# (tag, flags, distance): see tests/test_pacenotes.py for what each becomes
NOTES = [
    (3, 0x0, 198.0),      # left medium, first bend
    (16, 0x0, 320.0),     # over crest
    (19, 0x0, 335.0),     # bump
    (14, 0x0, 350.0),     # narrows
    (8, 0x400, 430.0),    # right medium long
    (17, 0x0, 520.0),     # ford
    (18, 0x0, 540.0),     # caution
    (20, 0x0, 560.0),     # jump
    (28, 0x0, 600.0),     # go straight
    (13, 0x0, 610.0),     # distance call (derived: skipped)
    (29, 0x0, 640.0),     # keep right
    (27, 0x0, 660.0),     # bridge
]
TRIGGER = (7, 500.0, 2.5)          # object id, distance, lead (s)


def _strip(points, tang_unused=None):
    """Quad strip vertices around the centreline: columns t = -VERGE, -ROAD_HALF, +ROAD_HALF, +VERGE."""
    n = len(points)
    verts = []
    for i, p in enumerate(points):
        a, b = points[max(i - 1, 0)], points[min(i + 1, n - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        lx, ly = -dy / ln, dx / ln                               # left normal
        for t in (VERGE, ROAD_HALF, -ROAD_HALF, -VERGE):         # left to right
            verts.append((p[0] + lx * t, p[1] + ly * t, p[2]))
    return verts


def _quad_tris(i0, cols, mats_for):
    """Triangles for quads between cross-section i0 and i0+1; columns 0..3. mats_for(col) -> triangle extras."""
    tris = []
    for c in range(3):
        a, b, d, e = i0 * 4 + c, i0 * 4 + c + 1, (i0 + 1) * 4 + c, (i0 + 1) * 4 + c + 1
        for v in ((a, b, d), (b, e, d)):
            tris.append(mats_for(c, v))
    return tris


GRAVEL_ROW = bytes([17] * 16)                       # run 0: every cell Gravel_Fine_Shallow_Dry-ish (id patched per entry)


@dataclass
class StageInfo:
    folder: Path
    stem: str
    length: float
    points: list


def mat_entries():
    """Nine entries (wear x wetness). Run 0 = gravel everywhere; run 1 = gravel for cells 0..7, grass for 8..15."""
    gravel = {"new": (17, 18, 19), "normal": (9, 10, 11), "worn": (1, 2, 3)}      # Shallow / Deep / Deeper
    grass = (105, 106, 107)                                                       # Grass_Medium dry/damp/wet
    out = []
    for wear in ("new", "normal", "worn"):
        for w, wet in enumerate(("dry", "damp", "wet")):
            g, gr = gravel[wear][w], grass[w]
            out.append((f"testrally standard {wet} {wear}", [bytes([g] * 16 + [0] * 240),
                                                              bytes([g] * 8 + [gr] * 8 + [0] * 240)]))
    return out


def build_col(points) -> bytes:
    verts = _strip(points)
    n = len(points)
    half = n // 2

    def cell(i_from, i_to, attr_blend: bool, graft: int, split: bool):
        # remap vertices to a local array
        vs = verts[i_from * 4:(i_to + 1) * 4]
        tris = []
        for i0 in range(i_from, i_to):
            def mats_for(c, v, i0=i0):
                lv = tuple(x - i_from * 4 for x in v)
                if c == 1:                                   # the road: gravel only
                    return dict(v=lv, attr_a=(0, 0, 0), attr_b=(0, 0, 0), mat=(0, 0), nib=((0, 0, 0), (0, 0, 0)))
                outer = [(x % 4) in (0, 3) for x in v]       # outer verge columns
                if attr_blend:                               # blend gravel -> grass with attr A
                    return dict(v=lv, attr_a=tuple(31 if o else 0 for o in outer), attr_b=(0, 0, 0), mat=(0, 1),
                                nib=((0, 0, 0), (15, 15, 15)))
                # cell lookup transition inside material 0 (run 1: gravel 0..7, grass 8..15)
                return dict(v=lv, attr_a=(0, 0, 0), attr_b=(0, 0, 0), mat=(1, 0),
                            nib=(tuple(15 if o else 0 for o in outer), (0, 0, 0)))
            tris += _quad_tris(i0 - 0, 4, mats_for)
        return dict(vertices=vs, triangles=tris, graft=graft, split=split)

    # cells: depth 1, path bit 0 / 1 (flags top byte = depth)
    cells = [cell(0, half, True, 0x01000000, False), cell(half, n - 1, False, 0x01000001, True)]
    # a puddle on the road near x ~ 300 along the north run, a ford quad across the road near the end, a pond off-road
    puddles = [[(258.0, 140.0, 1.0), (262.0, 140.0, 1.0), (262.0, 143.0, 1.0), (258.0, 143.0, 1.0)]]
    deep = [[(420.0, 254.0, 2.0), (428.0, 254.0, 2.0), (428.0, 266.0, 2.0), (420.0, 266.0, 2.0)],      # across the road
            [(100.0, -40.0, 0.0), (112.0, -40.0, 0.0), (112.0, -28.0, 0.0), (100.0, -28.0, 0.0)]]      # pond
    # brake wall: pairs (inner, outer) on the left side from section 4 to 9; segments 0..4 with flags
    pts = []
    for i in range(4, 10):
        v = verts[i * 4]                                     # col 0 = left verge edge (t = +6)
        j = verts[i * 4 + 0]
        pts += [(v[0], v[1]), (v[0] - 1.0, v[1] + 3.0)]
    wall = dict(points=pts, items=[(0, False, False), (1, False, False), (2, True, False), (3, False, True),
                                   (4, True, True)])
    return write_col(cells, puddles, deep, wall)


def build_classic_stage(folder: Path, offset: float = 0.0, with_rsf: bool = True, letters=("M",),
                        night_differs: bool = True, translate=(0.0, 0.0), mirror: bool = False,
                        same_mat_entries: bool = False, step: float = 20.0) -> StageInfo:
    """Write track-101_<L>.{trk,dls,col,mat,fnc,lbs,ini} (+ Tracks101.ini, TrackSettings101.ini) into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    pts = [(x + translate[0], (-y if mirror else y) + translate[1], z) for x, y, z in centreline(step)]
    tans = tangents(pts)
    dist = stored_distance(pts, offset)
    length = dist[-1]
    meshes = [
        dict(name="CONE.KSH", kind=1, material=0, dynamic=1, shape=("box", (-0.2, -0.2, 0), (0.2, 0.2, 0.7)),
             vertices=[(-0.2, -0.2, 0), (0.2, -0.2, 0), (0.2, 0.2, 0), (-0.2, 0.2, 0), (0, 0, 0.7)],
             faces=[(0, 0, 1, 4), (0, 1, 2, 4), (0, 2, 3, 4), (0, 3, 0, 4)],
             placements=[dict(position=(100.0, 5.0, 0.2), scale=(1, 1, 1), quat=(0, 0, 0, 1)),
                         dict(position=(104.0, 5.0, 0.2), scale=(1, 1, 1), quat=(0, 0, 0, 1)),
                         dict(position=(108.0, -5.0, 0.2), scale=(1, 1, 1), quat=(0, 0, 0, 1))]),
        dict(name="SIGN.KSH", kind=3, material=0, dynamic=0, shape=None,
             vertices=[(0, 0, 0), (0.1, 0, 0), (0.1, 0, 1.5), (0, 0, 1.5)],
             faces=[(0, 0, 1, 2), (0, 0, 2, 3)],
             placements=[dict(position=(150.0, 6.0, 0.4), scale=(1, 1, 1), quat=(0, 0, 0.7071, 0.7071))]),
        dict(name="HO_BUSH01.KSH", kind=0, material=165, dynamic=0, shape=("sphere", (0, 0, 1), 1.0),
             vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 2)],
             faces=[(0xFF, 0, 1, 2), (0, 0, 1, 3)],
             placements=[dict(position=(50.0, 9.0, 0.1), scale=(1, 1, 1), quat=(0, 0, 0, 1))]),
    ]
    keys = [(0x15, 0, MARKERS["start"] + offset)]
    keys += [(0x17, 0, s + offset) for s in MARKERS["splits"]]
    keys += [(0x16, 0, MARKERS["finish"] + offset), (0x18, 0, MARKERS["end"] + offset)]
    keys += [(t, f, d + offset) for t, f, d in NOTES]
    trig = (TRIGGER[0], TRIGGER[1] + offset, TRIGGER[2])
    fnc = write_fnc([
        dict(fence_type=0, post_type=4, material=0, rail_material=0,
             posts=[((x, 5.4, 0.3 * x / 10), 0xFFFFFFFF) for x in (60.0, 70.0, 80.0, 90.0, 100.0, 110.0)]),
        dict(fence_type=2, post_type=14, material=1, rail_material=1,
             posts=[((x, -5.8, 0.3 * x / 10), 0xFF808080) for x in (60.0, 70.0, 80.0, 90.0)]),
    ], textures=("fence_tape", "fence_net"))
    lbs = write_lbs(start=dict(position=(5.0, 0.15, 0.0), rotation=(0.0, math.pi / 2, 0.0)),
                    anim_objects=[dict(name="marshal01", ids=(7, 0, 1), scripts=("marshal.LUA", "")),
                                  dict(name="bird01", ids=(8, 0, 1), scripts=("bird.LUA", "bird2.LUA"))],
                    geometry=[LBS_STAGE_GEOMETRY])
    ini = (b"[TextureInfo]\r\nTexture0=dry\\new\\road.tga\r\nTexture1=verge.tga\r\n[road]\r\nMipLevels=4\r\nIsGroundTexture=1\r\n")
    for L in letters:
        stem = f"track-101_{L}"
        (folder / f"{stem}.trk").write_bytes(write_trk(list(zip(pts, tans, dist)), meshes))
        (folder / f"{stem}.dls").write_bytes(write_dls(keys, [trig], sphere=[(100.0, 0.0, 1.0, 5.0)],
                                                       named=[("start_animation2", 7)], extra_sets=("Camera",)))
        col = build_col(pts)
        mat = write_mat(mat_entries(), tail=b"CMAP\0\0\0\0")
        if same_mat_entries:
            e0 = mat_entries()[3][1]
            mat = write_mat([(n, e0) for n, _r in mat_entries()])
        if L == "N" and night_differs:
            mat = write_mat(mat_entries()[:6])          # a night variant with fewer .mat entries
        (folder / f"{stem}.col").write_bytes(col)
        (folder / f"{stem}.mat").write_bytes(mat)
        (folder / f"{stem}.fnc").write_bytes(fnc)
        (folder / f"{stem}.lbs").write_bytes(lbs)
        (folder / f"{stem}.ini").write_bytes(ini)
    if with_rsf:
        (folder / "Tracks101.ini").write_bytes(
            b"[Map101]\r\nTrackName=Maps\\101-Test Stage\\track-101\r\nStageName=Test Stage\r\nSurface=1\r\n"
            b"Length=0.73\r\nAuthor=Synthetic\r\nCountryCode=ES\r\nDefaultWetness=dry\r\n"
            b"AnimationPath=CustomData\\TestStage\r\nParticles=PS_British\r\n")
        sec = []
        for L in letters:
            sec += [f"[101{L}_hazy_clear]", f"[101{L}_lightrain_heavycloud]"]
        sec += ["[101O_E_hazy_partcloud] ; with a cloud letter", "[101M_heavysnow_heavycloud]", "[101M_bogus_weather]"]
        (folder / "TrackSettings101.ini").write_bytes(("\r\n".join(sec) + "\r\n").encode())
    return StageInfo(folder, "track-101", length, pts)


# ── the synthetic BTB folder ─────────────────────────────────────────────────

def _quad_mesh(w=20.0, d=20.0):
    return xfile.Mesh([(0, 0, 0), (w, 0, 0), (w, 0, d), (0, 0, d)], [(0, 2, 1), (0, 3, 2)],
                      [(0, 0), (1, 0), (1, 1), (0, 1)], ["TexDir\\Materials\\road"], [0, 0])


def build_btb_stage(folder: Path, glued: bool = True) -> Path:
    """A BTB stage folder: inis, `Objects.zip` with one ground tile, a box collider and a cone, `Textures.zip`.

    D3D frame (left-handed, Y up): the driveline is Z up (.trk-like), the objects are Y up, x,z horizontal.
    The road runs along +x of the Z-up driveline = +x of the D3D frame; y_stj = z_d3d.
    """
    folder.mkdir(parents=True, exist_ok=True)
    pts = centreline()
    dist = stored_distance(pts)
    tans = tangents(pts)
    (folder / "track.ini").write_bytes(
        b"[INFO]\r\nname=Synthetic BTB\r\nphysics=Gravel\r\nauthor=Test\xe9\r\nversion=1.0\r\ndate=2026-11-05\r\n"
        b"comment=a test\r\nlength=0,73\r\ncountrycode=FI\r\nsplashscreen=splash.jpg\r\n")
    dl = f"[DRIVELINE]\r\ncount={len(pts)}\r\n"
    for i, (p, t, d) in enumerate(zip(pts, tans, dist)):
        dl += f"K{i}=" + ", ".join(f"{v:.5f}" for v in (*p, *t, d, 0)) + "\r\n"
    (folder / "driveline.ini").write_bytes(dl.encode())
    notes = [(3, 0, 198.0), (8, 0x400, 430.0), (17, 0, 520.0), (21, 0, 30.0), (22, 0, 690.0), (23, 0, 240.0), (24, 0, 705.0)]
    pn = f"[PACENOTES]\r\ncount={len(notes)}\r\n"
    for i, (t, f, d) in enumerate(notes):
        pn += f"[P{i}]\r\ntype={t}\r\ndistance={d}\r\nflag={f}\r\n"
    pn += "[P50]\r\ntype=4\r\ndistance=100\r\nflag=0\r\n"                         # beyond `count`: ignored
    (folder / "pacenotes.ini").write_bytes(pn.encode())
    (folder / "spawn.ini").write_bytes(b"[SPAWN]\r\nm12=5\r\nm13=0.15\r\nm14=0\r\nangleZ=1.5708\r\n")
    (folder / "settings.ini").write_bytes(b"[SETTINGS]\r\nsunDirection=-0.5, -0.7, -0.5\r\n")
    # mat.ini: two maps; glued "69[MAP1]" at the end of MAP0's data15 (the map 1 header is lost, first wins)
    rows = lambda v: "".join(f"data{r}=" + " ".join([str(v)] * 16) + chr(13) + chr(10) for r in range(16))  # noqa: E731
    mat = "[MAPS]\r\ncount=2\r\n[MAP0]\r\ntexture=road.dds\r\n" + rows(17)
    if glued:
        mat = mat.rstrip("\r\n")[:-2] + "69[MAP1]\r\n"                              # the quirk: last cell 17 -> 69[MAP1]
    else:
        mat += "[MAP1]\r\ntexture=grass.dds\r\n" + rows(105)
    mat += "[MAP0]\r\ntexture=duplicate.dds\r\n"                                     # duplicate: first wins
    (folder / "mat.ini").write_bytes(mat.encode())
    (folder / "materials.ini").write_bytes((
        "[MATERIALS]\r\ncount=2\r\nMAT_0=TexDir\\Materials\\road\r\nMAT_1=TexDir\\Materials\\grass-blend2\r\n"
        "[TexDir\\Materials\\road]\r\neffectName=BTB_Diffuse\r\ntechniqueName=Default\r\n"
        "diffuseTexture=C:\\Users\\author\\road.dds\r\nSpecularColor=0,4235294,0,4235294\r\n"
        "[TexDir\\Materials\\grass]\r\neffectName=BTB_Diffuse\r\ntechniqueName=Default\r\n"
        "diffuseTexture=C:\\Users\\author\\grass.dds\r\n").encode())
    (folder / "objectlist.ini").write_bytes((
        "[OBJECTS]\r\ncount=4\r\n"
        "[Object_0]\r\nfile=ground\r\nxform=ground\r\nVisible=1\r\nCollisionModel=0\r\nLODIn=0\r\nLODOut=2679,576\r\n"
        "[Object_1]\r\nfile=box\r\nxform=box\r\nVisible=1\r\nCollisionModel=2\r\ncolBox=4, 2, 3\r\n"
        "[Object_2]\r\nfile=hidden\r\nxform=hidden\r\nVisible=0\r\nCollisionModel=0\r\n"
        "[Object_3]\r\nfile=cone\r\nxform=cone\r\nVisible=1\r\nCollisionModel=-1\r\nLODOut=300\r\n").encode())
    ground = _quad_mesh(40.0, 40.0)
    box = xfile.Mesh([(-1, 0, -1), (1, 0, -1), (1, 2, -1), (-1, 2, -1), (-1, 0, 1), (1, 0, 1), (1, 2, 1), (-1, 2, 1)],
                     [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6)], None, ["TexDir\\Materials\\grass"], [0] * 4)
    cone = xfile.Mesh([(-0.2, 0, -0.2), (0.2, 0, -0.2), (0, 0.7, 0)], [(0, 1, 2)], [(0, 0), (1, 0), (0.5, 1)],
                      ["TexDir\\Materials\\grass"], [0])
    ident = lambda x, y, z: (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1)  # noqa: E731
    with zipfile.ZipFile(folder / "Objects.zip", "w") as z:
        z.writestr("Objects/ground.x", xfile.write_binary(ground))
        z.writestr("Objects/ground.xform", xform.pack([ident(0, 0, -20), ident(40, 0.5, -20), ident(80, 1.0, -20)]))
        z.writestr("Objects/box.x", xfile.write_binary(box))
        z.writestr("Objects/box.xform", xform.pack([ident(100, 0, 8)]))
        z.writestr("Objects/hidden.x", xfile.write_binary(_quad_mesh()))
        z.writestr("Objects/hidden.xform", xform.pack([ident(0, 0, 0)]))
        z.writestr("Objects/cone.x", xfile.write_binary(cone))
        z.writestr("Objects/cone.xform", xform.pack([ident(50, 0, 6), ident(54, 0, 6)]))
    with zipfile.ZipFile(folder / "Textures.zip", "w") as z:
        z.writestr("road.dds", b"DDS ")
        z.writestr("grass.dds", b"DDS ")
    return folder
