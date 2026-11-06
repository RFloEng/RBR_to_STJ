"""BTB stage folder -> IR.

Frames (reRBR notes/btb.md "Drawing"): the driveline is Z up like `.trk` (STJ frame, unchanged); `.x` vertices,
`.xform` matrices and `spawn.ini` are Direct3D: left-handed, Y up, row vectors (`p * M`, translation in elements
12..14). D3D (x, y, z) -> STJ (x, z, y). That swap flips handedness, so triangle winding is reversed to keep the
outward normals right.

Collision (notes/btb.md "Collision"): objects with `CollisionModel=0` and `Visible` not 0 give the ground faces, one
copy per `.xform` matrix; `CollisionModel` 1 or 2 are box colliders sized by `colBox`. A face's surface: the mesh's
material name -> its `materials.ini` section -> base name of `diffuseTexture` -> the first `mat.ini` map whose
`texture=` base name matches (0 if none) -> `cells[int(u * 15) & 15]` of that map's first row.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from pathlib import Path

from ..errors import FormatError, Rbr2StjError
from ..ir import CollisionMesh, Info, Line, Pose, Prop, Stage, SurfaceState, Timing
from ..readers import btb
from . import Options
from .classic import LIMIT_M, build_line, chainage_check, clamp_to_line, tangent_check, _load_physics
from .pacenotes import RawNote, convert_notes, direction_check
from .project import RefLine
from .surfaces import pack_state, top3


def d3d_to_stj(p):
    return (p[0], p[2], p[1])


def transform_point(m, p):
    """Row-vector D3D transform p * M for a 4x4 row-major matrix m (16 floats)."""
    x, y, z = p
    return (x * m[0] + y * m[4] + z * m[8] + m[12], x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


def _material_map_index(track: btb.BtbTrack, texture_name: str) -> int:
    """mesh material (a `.x` TextureFilename) -> materials.ini section -> diffuse base name -> mat.ini map index."""
    sec = track.materials.get(texture_name.lower())
    diffuse = btb._base(sec.diffuse) if sec and sec.diffuse else None
    return track.map_index_for_texture(diffuse)


def mesh_faces_surfaces(track: btb.BtbTrack, mesh):
    """Per face: (ids3, corner weights), from the vertex UVs (computed once per mesh, shared by all its clones)."""
    maps_n = len(track.map_rows)
    names = mesh.material_names
    mat_idx = [_material_map_index(track, n) for n in names] or [0]
    cell = []
    for v in range(len(mesh.vertices)):
        if mesh.uvs is None:
            cell.append(0)                                   # a mesh without UVs reads 0
        else:
            cell.append(int(mesh.uvs[v][0] * 15) & 0xF)
    out = []
    for fi, tri in enumerate(mesh.triangles):
        fm = mesh.face_materials[fi] if fi < len(mesh.face_materials) else 0
        mi = mat_idx[fm] if fm < len(mat_idx) else mat_idx[0]
        rows = track.map_rows[mi][0] if mi < maps_n else [0] * 16
        corners = [{rows[cell[v]]: 1.0} for v in tri]
        out.append(top3(corners))
    return out


def _scale_unit_tangents(line, diag):
    """BTB drivelines store a direction of about unit length per segment, while STJ tangents are dP/du over the segment
    (about one chord long, spec 7.1). When the stored tangents are far shorter than their chords, each is scaled to the
    chord of its own segment (direction kept); the stored values stay in x_rbr.tangents_raw."""
    r = diag.get("tangent_to_chord_ratio")
    if not r or r["median"] >= 0.5:
        return
    out = []
    for i, t in enumerate(line.tangents_raw):
        j = min(i, len(line.points) - 2)
        chord = math.dist(line.points[j], line.points[j + 1])
        n = math.dist((0, 0, 0), t)
        out.append(tuple(c * chord / n for c in t) if n > 1e-9 else tuple(b - a for a, b in zip(line.points[j], line.points[j + 1])))
    line.tangents = out
    line.tangents_in = [out[0]] + out[:-1]
    diag["tangents_scaled_to_chord"] = True


def convert_btb(path, opt: Options | None = None) -> Stage:
    opt = opt or Options()
    t = btb.open_folder(path)
    lost: list[str] = []
    diag: dict = {}

    pts = [p.position for p in t.driveline]
    tans = [p.tangent for p in t.driveline]
    dist = [p.distance for p in t.driveline]
    if len(pts) < 2:
        raise FormatError("driveline.ini: a stage needs at least two driveline points")
    line = build_line(pts, tans, dist, lost, diag)
    origin = None
    if max(max(abs(p[0]), abs(p[1])) for p in line.points) > LIMIT_M:
        cx = round(sum(p[0] for p in line.points) / len(line.points))
        cy = round(sum(p[1] for p in line.points) / len(line.points))
        origin = (float(cx), float(cy), 0.0)
        line.points = [(p[0] - cx, p[1] - cy, p[2]) for p in line.points]
    ox, oy = (origin[0], origin[1]) if origin else (0.0, 0.0)

    def sh(p):                       # STJ-frame point -> shifted
        return (p[0] - ox, p[1] - oy, p[2])

    tangent_check(line, diag)
    _scale_unit_tangents(line, diag)
    chainage_check(line, diag)
    ref = RefLine(line.points, line.tangents, line.chainage, line.tangents_in)
    off = line.chainage_offset

    info = Info(name=t.name or Path(path).name, surface=t.physics if t.physics in ("gravel", "tarmac", "snow") else None,
                country=t.country, author=t.author, version=t.version, date=t.date, comment=t.comment)
    if t.physics is None:
        lost.append("track.ini has no physics= value; the primary surface is unknown (RBR then plays it as gravel)")
    info.length_m = round(ref.length, 1)

    # timing and notes: pacenotes.ini keys; tags 21..24 are start / finish / split / end (as in the built .dls)
    def last(tag):
        for n in sorted(t.notes, key=lambda n: n.distance, reverse=True):
            if n.tag & 0xFFFF == tag:
                return n.distance
        return None

    start, finish, end = last(21), last(22), last(24)
    splits = sorted(n.distance for n in t.notes if n.tag & 0xFFFF == 23)[:16]
    timing = Timing(None if start is None else start - off, None if finish is None else finish - off,
                    None if end is None else end - off, [s - off for s in splits])
    for name, v in (("start", timing.start), ("finish", timing.finish)):
        if v is None:
            lost.append(f"pacenotes.ini has no {name} key (tag {21 if name == 'start' else 22}), so timing.{name} is missing")
    raw_notes = [RawNote(n.tag, n.flags, n.distance - off) for n in t.notes]

    texture_source = None
    if opt.textures and not opt.no_geometry:
        from .. import textures as _tex
        from ..readers.stage_files import in_dir
        tz = in_dir(t.folder, "Textures.zip")
        texture_source = _tex.TextureSource([tz] if tz else [], [], [])
    stage = Stage(kind="rbr-btb", info=info, source=f"{t.folder.name}", line=line, timing=timing, raw_notes=raw_notes,
                  lost=lost, diagnostics=diag)
    stage.origin_offset = origin
    _load_physics(stage, t.folder, opt)
    stage.texture_source = texture_source
    stage.shift = (ox, oy)
    folder = t.folder
    stage.file_hashes = {"ini": hashlib.sha256((folder / "driveline.ini").read_bytes()).hexdigest()}
    stage.x_rbr = {"track": t.folder.name, "chainage_offset": off,
                   "tangents_raw": [[round(c, 6) for c in tg] for tg in line.tangents_raw],
                   "physics": t.physics, "splashscreen": t.splashscreen, "date": t.date, "version": t.version,
                   "sun_direction": list(t.sun_direction) if t.sun_direction else None,
                   "textures": t.textures[:2000]}
    if t.spawn is not None:
        stage.spawn = Pose(sh(d3d_to_stj(t.spawn)), math.pi / 2 - t.spawn_angle_z)

    if not opt.no_geometry:
        _objects(stage, t, opt, sh, lost)
    notes, skipped = convert_notes(stage.raw_notes)
    stage.x_rbr["notes_skipped"] = skipped
    stage.notes = notes
    clamp_to_line(stage)
    diag["pacenotes"] = {"count": len(notes), "direction_vs_geometry": direction_check(notes, ref)}
    lost.append("weather and sky: a BTB stage borrows RBR map 41's sky and weather rows, so STJ has no weather data")
    lost.append("BTB shaders (effects/techniques are kept by name in the visual materials only)")
    return stage


def _box_geometry(a, b, c):
    """Collision box (notes/btb.md "Collision meshes"): colBox (a, b, c): x half a/2, y half c/2 (STJ axes), z from
    -0.05 b to b. Returns 8 corners (bit 1 = x, 2 = y, 4 = z) and 12 outward-facing triangles."""
    ex, ey, ez = a * 0.5, c * 0.5, b * 0.5
    zs = (-ez * 0.1, ez + ez)
    verts = [((-ex, ex)[i & 1], (-ey, ey)[(i >> 1) & 1], zs[(i >> 2) & 1]) for i in range(8)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    faces = []
    for q in quads:
        faces += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    return verts, faces


def _objects(stage: Stage, t: btb.BtbTrack, opt: Options, sh, lost: list[str]):
    pts: list = []
    faces: list = []
    per_face: list = []
    boxes: list[Prop] = []
    visual: list[dict] = []
    skipped: dict[str, int] = defaultdict(int)
    n_col_objects = 0
    n_tilted = 0
    meshes_cache: dict[str, tuple] = {}
    obj_summary = []

    def get_mesh(name):
        if name in meshes_cache:
            return meshes_cache[name]
        try:
            m = t.mesh(name)
        except FormatError as e:
            skipped[f"unreadable .x: {e}"] += 1
            m = None
        meshes_cache[name] = (m, None)
        return meshes_cache[name]

    for o in t.objects:
        visible = o.visible is not False
        try:
            mats = t.placements(o.xform)
        except FormatError as e:
            skipped[f"bad .xform: {e}"] += 1
            continue
        if mats is None:
            mats = [(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)]     # no xform file: one identity clone
        cm = o.collision_model
        obj_summary.append({"index": o.index, "file": o.file, "xform_count": len(mats), "visible": visible,
                            "collision_model": cm, "lod_in": o.lod_in, "lod_out": o.lod_out})
        mesh, _ = get_mesh(o.file) if o.file else (None, None)
        if mesh is None:
            if o.file:
                skipped["mesh missing in Objects.zip or unreadable"] += 1
            continue
        if cm == 0 and visible:
            n_col_objects += 1
            surf = mesh_faces_surfaces(t, mesh)
            # handedness flip: reverse each triangle (a, b, c) -> (a, c, b); its corner weights follow the order
            rev = [((a, c, b), (ids, [ws[0], ws[2], ws[1]])) for (a, b, c), (ids, ws) in zip(mesh.triangles, surf)]
            for m in mats:
                base = len(pts)
                for v in mesh.vertices:
                    pts.append(sh(d3d_to_stj(transform_point(m, v))))
                for (a, b, c), fs in rev:
                    faces.append((base + a, base + b, base + c))
                    per_face.append(fs)
        elif cm in (1, 2) and visible:
            a_b_c = o.col_box
            if a_b_c is None:
                bb = list(zip(*mesh.vertices)) if mesh.vertices else [(0,), (0,), (0,)]
                a_b_c = (max(bb[0]) - min(bb[0]), max(bb[1]) - min(bb[1]), max(bb[2]) - min(bb[2]))
                skipped["box collider without colBox sized from the mesh bounds"] += 1
            verts, bfaces = _box_geometry(*a_b_c)
            groups: dict[tuple, list] = defaultdict(list)
            for m in mats:
                sx = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2) or 1.0
                sz = math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2) or 1.0   # D3D y row = STJ z
                sy = math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2) or 1.0  # D3D z row = STJ y
                yaw = math.atan2(m[2], m[0])
                if abs(m[1]) / sx > 0.02 or abs(m[6]) / sz > 0.02 or abs(m[9]) / sy > 0.02:
                    n_tilted += 1
                x, y, z = sh(d3d_to_stj((m[12], m[13], m[14])))
                groups[(round(sx, 3), round(sy, 3), round(sz, 3))].append((x, y, z, yaw, 1.0))
            for (sx, sy, sz), tfs in groups.items():
                v = [(p[0] * sx, p[1] * sy, p[2] * sz) for p in verts]
                boxes.append(Prop("box", f"box_{o.index}", 0, False, ("box", (-a_b_c[0] * sx / 2, -a_b_c[2] * sy / 2, 0),
                                                                      (a_b_c[0] * sx / 2, a_b_c[2] * sy / 2, a_b_c[1] * sz)),
                                  v, bfaces, tfs, [{"scale": [sx, sy, sz]}]))
        if opt.visual and visible and mesh is not None:
            visual.append({"object": o, "mesh": mesh, "matrices": mats})

    if skipped:
        stage.diagnostics["skipped_objects"] = dict(skipped)
        lost.append("some objects were skipped: " + "; ".join(f"{k} ({v})" for k, v in skipped.items()))
    if n_tilted:
        lost.append(f"{n_tilted} box collider(s) are tilted (pitch/roll); only their heading is kept")
    if pts:
        palette_state = pack_state(per_face)
        palette, layers, weights = palette_state
        st = SurfaceState("all", "normal", "dry", palette, layers, weights, [("normal", "dry")])
        stage.collision = CollisionMesh(pts, faces, [st])
        stage.diagnostics["collision"] = {"objects": n_col_objects, "triangles": len(faces), "vertices": len(pts)}
        lost.append("BTB surface cell lookup is sampled at the triangle corners; a surface change inside a triangle "
                    "is reduced to its corner values")
    elif t.objects:
        lost.append("no visible CollisionModel=0 object produced ground faces, so there is no collision mesh")
    stage.props = boxes
    stage.visual = visual
    stage.btb_track = t
    stage.x_btb["objects"] = obj_summary
    stage.x_btb["materials"] = {k: {"name": m.name, "effect": m.effect, "technique": m.technique,
                                    "diffuse": (m.diffuse or "").replace("\\", "/").rsplit("/", 1)[-1]}
                                for k, m in t.materials.items()}
    stage.diagnostics["objects"] = {"listed": len(t.objects), "visual_written": len(visual)}
