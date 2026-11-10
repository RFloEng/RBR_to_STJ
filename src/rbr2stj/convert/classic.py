"""Classic RBR stage (`track-NN_<L>.{trk,dls,col,mat,fnc,lbs,ini}`) -> IR.

Frames (reRBR notes/formats.md "Stage geometry"): `.trk`, the driveline, `.col` and `.fnc` are Z up and right-handed,
which is the STJ frame, so their coordinates pass through unchanged. `.lbs` is Y up and left-handed (the `.trk` frame
with y and z swapped): lbs (x, y, z) -> STJ (x, z, y); a car heading `yaw` about lbs Y (forward (sin yaw, 0, cos yaw))
becomes heading pi/2 - yaw.
"""

from __future__ import annotations

import hashlib
import math
import re

from .. import __version__
from ..errors import FormatError, Rbr2StjError
from ..ir import (CollisionMesh, Fence, Info, Line, Pose, Prop, Stage, SurfaceState, Timing, Weather, WaterBody)
from ..readers import col, dls, fnc, lbs, mat, rsf, stage_files, surfaces_table, trk
from . import Options
from .edges import brake_wall_edges, fence_edges, polygon_crosses_line
from .pacenotes import RawNote, convert_notes, direction_check
from .project import RefLine
from .surfaces import corner_weights, pack_state, top3

WEAR_VALUE = {"new": 0.0, "normal": 0.5, "worn": 1.0}
WET_VALUE = {"dry": 0.0, "damp": 0.5, "wet": 1.0}
KIND_NAME = trk.KIND_NAMES
LIMIT_M = 10_000.0


def lbs_to_stj(p):
    return (p[0], p[2], p[1])


# .lbs (2,14) scripted objects: the load script names what the object is. Placed as STJ race-control posts and vehicles;
# birds, deer and the stage's own start/finish scenes are not people or vehicles of the event and stay in x_rbr.
_POST_KIND = (("spectator", "spectator_point"), ("marshal", "flag_post"), ("media", "other"), ("mechanic", "other"))


def _scripted_marshals(objects, sh, x_rbr: dict) -> dict | None:
    posts, vehicles, counts = [], [], {}
    for i, o in enumerate(objects):
        load = o.scripts[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if o.scripts and o.scripts[0] else ""
        label = None
        if "rescuechopper" in load:
            kind, target = "helicopter", vehicles
        else:
            label, kind = next(((key, k) for key, k in _POST_KIND if key in load), (None, None))
            target = posts
        if kind is None or len(o.vectors) < 2:
            counts["not_placed:" + (load or o.name)] = counts.get("not_placed:" + (load or o.name), 0) + 1
            continue
        pos = sh(lbs_to_stj(o.vectors[0]))
        yaw = math.radians(o.vectors[1][1])
        rec = {"id": f"{label or 'helicopter'}_{i + 1}", "kind": kind, "x_rbr": {
            "object": o.ids[0], "name": o.name, "scripts": [x for x in o.scripts if x], "yaw_deg": round(o.vectors[1][1], 3)}}
        if target is vehicles:
            rec["pose"] = {"position": [round(c, 4) for c in pos], "heading": round(math.pi / 2 - yaw, 6)}
        else:
            rec["position"] = [round(c, 4) for c in pos]
        target.append(rec)
        counts[kind] = counts.get(kind, 0) + 1
    x_rbr["scripted_objects"] = counts
    out = {}
    if posts:
        out["posts"] = posts
    if vehicles:
        out["vehicles"] = vehicles
    return out or None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _yaw_from_quat(q):
    x, y, z, w = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _texture_map(ini_bytes: bytes) -> dict:
    from .. import ini as _ini
    out: dict[int, str] = {}
    ti = _ini.find_section(_ini.parse(ini_bytes), "TextureInfo")
    if ti is not None:
        for k, v in ti.entries:
            m = re.fullmatch(r"texture(\d+)", k.lower())
            if m:
                out.setdefault(int(m.group(1)), v)
    return out


def _global_sky(stage, sset):
    """The shared sky / cloud domes every classic stage draws: `Clouds.lbs` and `skyboxes.lbs` in the Maps folder, each
    with its own `.ini` texture list. All variants are written; the ones the first weather names are visible."""
    from ..readers import lbs_extras
    from ..readers.chunks import read_chunks
    maps = sset.folder.parent if sset.folder.parent.name.lower() == "maps" else sset.folder
    for stem in ("skyboxes", "Clouds"):
        f = next((p for p in (maps / f"{stem}.lbs", maps / f"{stem.lower()}.lbs") if p.is_file()), None)
        if f is None:
            continue
        try:
            items = lbs_extras.parse(read_chunks(f.read_bytes(), f.name)).items
        except Exception as e:                      # a broken shared file must not stop the stage
            stage.lost.append(f"{f.name}: not read ({e})")
            continue
        ini_p = f.with_suffix(".ini")
        tex = _texture_map(ini_p.read_bytes()) if ini_p.is_file() else {}
        stage.sky_files.append((f.name, tex, items))


def build_line(points, tangents, distances, lost, diag):
    """Driveline -> Line with chainage shifted to start at 0. Falls back to recomputed arc length when the stored
    distance is not strictly increasing (STJ requires it) and says so."""
    off = distances[0]
    chain = [d - off for d in distances]
    ok = all(b > a for a, b in zip(chain, chain[1:]))
    if not ok:
        chain = [0.0]
        for a, b in zip(points, points[1:]):
            chain.append(chain[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        lost.append("stored driveline distance was not strictly increasing; s was recomputed from the geometry")
        diag["chainage_fallback"] = True
        off = 0.0
    raw = [tuple(t) for t in tangents]
    # the game stores one tangent per segment: it starts segment i (tangents_out[i]) and ends it (tangents_in[i + 1])
    t_in = [raw[0]] + raw[:-1] if raw else raw
    return Line([tuple(p) for p in points], list(raw), chain, off, raw, t_in)


def tangent_check(line: Line, diag):  # raw per-segment tangents are checked against the chords
    """Hermite tangents are dP/du over a segment, so they should be about one chord long. reRBR's game code feeds
    the raw values straight into the Hermite formula (ngp/drive_line.rs `hermite_point`), so no scaling is applied;
    this only records the ratio so real data can confirm it."""
    ratios = []
    for i in range(len(line.points) - 1):
        chord = math.dist(line.points[i], line.points[i + 1])
        if chord > 1e-6:
            ratios.append(math.dist((0, 0, 0), line.tangents_raw[i]) / chord)
    if ratios:
        ratios.sort()
        diag["tangent_to_chord_ratio"] = {"min": round(ratios[0], 3), "median": round(ratios[len(ratios) // 2], 3),
                                          "max": round(ratios[-1], 3)}


def chainage_check(line: Line, diag):
    rl = RefLine(line.points, line.tangents, None, line.tangents_in)
    diag["chainage_vs_geometry"] = {
        "stored_length": round(line.chainage[-1], 3), "xy_length": round(rl.xy_length(), 3),
        "length_3d": round(rl.length_3d(), 3)}


def convert_classic(path, opt: Options | None = None) -> Stage:
    opt = opt or Options()
    sset, letters = stage_files.open_stage(path, opt.variant)
    lost: list[str] = []
    diag: dict = {}
    raw = {ext: sset.read(ext, required=(ext in stage_files.REQUIRED)) for ext in
           ("trk", "dls", "col", "mat", "fnc", "lbs", "ini")}
    t = trk.parse(raw["trk"])
    d = dls.parse(raw["dls"])
    mk = dls.markers(d)

    pts = [p.position for p in t.driveline]
    tans = [p.tangent for p in t.driveline]
    dist = [p.distance for p in t.driveline]
    line = build_line(pts, tans, dist, lost, diag)

    # re-origin when coordinates exceed 10 km (float32 precision, spec §4.4)
    origin = None
    if max(max(abs(p[0]), abs(p[1])) for p in line.points) > LIMIT_M:
        cx = round(sum(p[0] for p in line.points) / len(line.points))
        cy = round(sum(p[1] for p in line.points) / len(line.points))
        origin = (float(cx), float(cy), 0.0)
        line.points = [(p[0] - cx, p[1] - cy, p[2]) for p in line.points]
    ox, oy = (origin[0], origin[1]) if origin else (0.0, 0.0)

    def sh(p):
        return (p[0] - ox, p[1] - oy, p[2])

    tangent_check(line, diag)
    chainage_check(line, diag)
    ref = RefLine(line.points, line.tangents, line.chainage, line.tangents_in)
    off = line.chainage_offset

    # info: RSF Tracks<id>.ini, else the retail name
    number = int(sset.track.split("-")[1])
    rinfo = rsf.read_folder(sset.folder)
    info = Info(name=sset.stem, letter=sset.letter, id=number)
    maps_ini = sset.folder / "Tracks.ini"
    if rinfo:
        info.name = rinfo.name or sset.stem
        info.surface, info.country, info.author = rinfo.surface, rinfo.country, rinfo.author
        info.default_wetness = rinfo.default_wetness
        if rinfo.length_km:
            info.length_m = rinfo.length_km * 1000.0
        info.id = rinfo.id
    elif maps_ini.exists():
        info.name = rsf.retail_name(sset.folder, number) or info.name
    if info.length_m is None:
        info.length_m = round(ref.length, 1)

    timing = Timing(
        start=None if mk.start is None else mk.start - off,
        finish=None if mk.finish is None else mk.finish - off,
        stop=None if mk.end is None else mk.end - off,
        splits=[s - off for s in mk.splits])
    for name, v in (("start", timing.start), ("finish", timing.finish)):
        if v is None:
            lost.append(f"the .dls has no {name} key, so timing.{name} is missing")
    raw_notes = [RawNote(k.tag, k.flags, k.distance - off) for k in mk.notes]

    stage = Stage(kind="rbr-classic", info=info, source=f"{sset.stem} ({sset.folder.name})", line=line, timing=timing,
                  raw_notes=raw_notes, lost=lost, diagnostics=diag)
    stage.origin_offset = origin
    _load_physics(stage, sset.folder, opt)
    stage.file_hashes = {k: _sha(v) for k, v in raw.items() if v is not None}
    stage.x_rbr = {
        "track": sset.stem, "variant": sset.letter, "variants_available": letters, "chainage_offset": off,
        "tangents_raw": [list(map(lambda c: round(c, 6), tg)) for tg in line.tangents_raw],
        "triggers": [{"object": tr.object, "s": round(tr.distance - off, 3), "lead": tr.lead} for tr in d.triggers],
        "plain_triggers": [{"object": tr.object, "s": round(tr.distance - off, 3), "flags": tr.flags}
                           for tr in d.plain_triggers],
        "named_triggers": [{"name": n, "object": o} for n, o in d.named_triggers],
        "sound_spheres": [{"position": [round(c, 3) for c in sh(s[:3])], "radius_word": s[3]} for s in d.spheres],
    }
    if rinfo:
        stage.x_rbr["rsf"] = {k: v for k, v in {"particles": rinfo.particles, "animation_path": rinfo.animation_path,
                                                 "id": rinfo.id}.items() if v is not None}
        stage.weathers = [Weather(w.settings, w.section, w.letter, w.cloud_letter, w.preset, w.cloud) for w in rinfo.weathers]
    if raw["ini"]:
        from .. import ini as _ini
        secs = _ini.parse(raw["ini"])
        ti = _ini.find_section(secs, "TextureInfo")
        if ti is not None:
            stage.x_rbr["textures"] = [v for k, v in ti.entries if k.lower().startswith("texture")]
            for k, v in ti.entries:
                m = re.fullmatch(r"texture(\d+)", k.lower())
                if m:
                    stage.texture_requests.setdefault(int(m.group(1)), v)
                m = re.fullmatch(r"shadowtexture(\d+)", k.lower())
                if m:
                    stage.shadow_requests.setdefault(int(m.group(1)), v)
                m = re.fullmatch(r"speculartexture(\d+)", k.lower())
                if m:
                    stage.specular_requests.setdefault(int(m.group(1)), v)

    if opt.textures and not opt.no_geometry:
        from .. import textures as _tex
        stage.texture_source = _tex.for_classic(sset.folder, sset.track, sset.letter, _tex.remap_targets(sset.folder))

    # spawn from .lbs (2,11)
    if raw["lbs"]:
        lb = lbs.parse(raw["lbs"])
        if lb.start is not None and any(lb.start.position):
            r = lb.start.rotation
            yaw = r[1] if abs(r[0]) < 1e-6 and abs(r[2]) < 1e-6 else math.sqrt(sum(c * c for c in r))
            stage.spawn = Pose(sh(lbs_to_stj(lb.start.position)), math.pi / 2 - yaw)
        if lb.visibility:
            stage.x_rbr["visibility_cells"] = {
                "note": "RBR renderer culling data (.lbs 2,10 and 2,18; cell i is geometry block i and lane i): two vectors per "
                        "cell in the geometry frame (Y up, left-handed), not converted; STJ has no culling-cell slot",
                "cells": [{"a": list(a), "b": list(b)} for a, b in lb.visibility],
                "visible_objects": [{"vectors": [list(v) for v in vs], "words": list(w)} for vs, w in lb.visible_objects]}
        if lb.containers:
            stage.x_rbr["containers"] = [{"name": n, "words": list(w)} for n, w in lb.containers]
        if lb.track_vectors:
            stage.x_rbr["track_vectors"] = [list(v) for v in lb.track_vectors]
        stage.marshals = _scripted_marshals(lb.animation_objects, sh, stage.x_rbr)
        stage.x_rbr["animation_objects"] = [
            {"name": o.name, "ids": list(o.ids), "scripts": [x for x in o.scripts if x]} for o in lb.animation_objects]
        if opt.visual and not opt.no_geometry and lb.geometry:
            stage.lbs_visual = lb.geometry
            stage.shift = (ox, oy)
            from ..readers import lbs_extras
            from ..readers.chunks import read_chunks
            ex = lbs_extras.parse(read_chunks(raw["lbs"], ".lbs"))
            stage.lbs_extras = ex.items
            stage.x_rbr["object_lane_flags"] = ex.lane_flags
            _global_sky(stage, sset)
            if lb.skipped_chunks:
                lost.append(f"{lb.skipped_chunks} render chunk(s) use a vertex layout rbr2stj does not decode")

    if not opt.no_geometry:
        _geometry(stage, t, raw, opt, sh, ref, lost, d)
    if info.surface is None and stage.collision and stage.collision.states:
        # retail stages carry no surface word: take the class most faces are made of (first layer of the first state)
        st0 = stage.collision.states[0]
        hist: dict[str, int] = {}
        for lay in st0.layers:
            cls, _r = surfaces_table.classify(st0.palette[lay[0]])
            key = {"asphalt": "tarmac", "gravel": "gravel", "snow": "snow"}.get(cls)
            if key:
                hist[key] = hist.get(key, 0) + 1
        if hist:
            info.surface = max(hist, key=hist.get)
            stage.diagnostics["primary_surface_from_collision"] = hist
    if stage.lbs_visual:
        far = sum(1 for b in stage.lbs_visual for c in b.chunks if c.draw_list == 3)
        lost.append(f"classic visual geometry: {far} far-LOD render chunks (draw list 3) are written as a `lod` mesh of the "
                    "near one (the 600 m switch distance is the renderer's documented value, not stored); sky and cloud "
                    "domes, object lanes, interactive objects, reflection and water faces are converted (water faces as "
                    "non-physical water); shadow maps (shadow_N.dat, written as greyscale PNG with the same texels) and specular maps "
                    "are carried per material (occlusion and specularColor inputs, `stj:shadowMap` / `stj:specularMap`) and "
                    "named in `baked`; the RBR shaders themselves (the exact blend of the second texture, the specular "
                    "power and colour) are not reproduced: the second texture of a chunk is copied and recorded only; baked "
                    "vertex light is kept as displayColor and marked baked.vertex_colour (never applied); lane sway is "
                    "carried as per-vertex weight and phase primvars with a scenery wind block (amplitude in metres and "
                    "unit of the stored frequency are not in the files); visibility cells, containers and track vectors "
                    "are kept in x_rbr (no STJ slot); scripted objects become marshals posts and vehicles where their "
                    "load script says what they are (spectators, marshals, media, rescue helicopter), the rest (birds, "
                    "deer, start and finish scenes) stay in x_rbr; textures are copied unchanged into textures/ and "
                    "listed in assets[]")
    else:
        lost.append("classic visual geometry (.lbs render chunks, textures, sky): not converted (no .lbs geometry or "
                    "--no-geometry); only the car start and animation object names are read from the .lbs")
    _finish(stage, ref)
    return stage


def _geometry(stage, t, raw, opt, sh, ref, lost, d):
    # collision mesh + surface states
    cf = col.parse(raw["col"] or b"") if raw["col"] else None
    if cf is not None:
        if cf.superseded_cells:
            lost.append(f"{cf.superseded_cells} .col cell(s) were overwritten by a later cell on the same graft "
                        "(as the game does) and are not in the collision mesh")
        points, faces, tris = [], [], []
        for cell in cf.cells:
            base = len(points)
            points += [sh(v) for v in cell.vertices]
            for tr in cell.triangles:
                faces.append((base + tr.v[0], base + tr.v[1], base + tr.v[2]))
                tris.append(tr)
        mesh = CollisionMesh(points, faces)
        mf = mat.parse(raw["mat"]) if raw["mat"] else None
        if mf is not None and tris:
            mesh.states = _states(mf, tris, opt, stage, lost)
        stage.collision = mesh
        stage.diagnostics["collision"] = {"cells": len(cf.cells), "triangles": len(faces), "vertices": len(points)}
        for q in cf.puddles:
            outline = [(sh(c)[0], sh(c)[1]) for c in q]
            stage.water.append(WaterBody("puddle", outline, round(sum(sh(c)[2] for c in q) / 4, 3), when_wet=True))
        for q in cf.deep_water:
            outline = [(sh(c)[0], sh(c)[1]) for c in q]
            kind = "ford" if polygon_crosses_line(outline, ref) else "pond"
            stage.water.append(WaterBody(kind, outline, round(sum(sh(c)[2] for c in q) / 4, 3)))
        if cf.brake_wall:
            w = col.BrakeWall([(sh((p[0], p[1], 0))[0], sh((p[0], p[1], 0))[1]) for p in cf.brake_wall.points],
                              cf.brake_wall.items)
            stage.edges += brake_wall_edges(w, ref)
    # props from .trk (3,0x16)
    stage.props = _props(t, sh)
    # fences
    if raw["fnc"]:
        ff = fnc.parse(raw["fnc"])
        stage.fences = [Fence(g.fence_type, g.post_type, g.material, g.rail_material,
                              [sh(p.position) for p in g.posts], [p.colour for p in g.posts]) for g in ff.groups]
        stage.edges += fence_edges(stage.fences, ref)
        if ff.textures:
            stage.x_rbr["fence_textures"] = ff.textures


def _states(mf, tris, opt, stage, lost):
    wetn = stage.info.default_wetness
    wanted = [("new", "dry"), ("new", "damp"), ("new", "wet"), ("normal", "dry"), ("normal", "damp"), ("normal", "wet"),
              ("worn", "dry"), ("worn", "damp"), ("worn", "wet")]
    if opt.surface_states == "default":
        wanted = [("normal", ("dry", "damp", "wet")[wetn if wetn in (0, 1, 2) else 0])]
    states: list[SurfaceState] = []
    seen: dict[tuple, SurfaceState] = {}
    for wear, wet in wanted:
        idx = mat.select(mf.entries, wear, wet)
        if idx is None:
            lost.append(f"no .mat entry for the state {wear}/{wet}: that surface map is not written")
            continue
        entry = mf.entries[idx]
        per_face = []
        for tr in tris:
            corners = []
            for c in range(3):
                s0 = mat.surface_at(entry, tr.mat[0], tr.nib[0][c])
                s1 = mat.surface_at(entry, tr.mat[1], tr.nib[1][c])
                corners.append(corner_weights(s0, s1, tr.attr_a[c] / 31.0))
            per_face.append(top3(corners))
        palette, layers, weights = pack_state(per_face)
        key = f"{wear}_{wet}"
        sig = (tuple(palette), tuple(layers), tuple(weights))
        if sig in seen:
            seen[sig].merged.append((wear, wet))
            stage.diagnostics.setdefault("identical_states", []).append([seen[sig].key, key])
            continue
        st = SurfaceState(key, wear, wet, palette, layers, weights, [(wear, wet)])
        seen[sig] = st
        states.append(st)
    if not states:
        raise Rbr2StjError("the .mat file has no entry for any wear/wetness state (new/normal/worn x dry/damp/wet)")
    return states


def _props(t, sh):
    props = []
    for m in t.meshes:
        kind = KIND_NAME.get(m.kind, f"kind{m.kind}")
        tf, raws = [], []
        for pl in m.placements:
            sx, sy, sz = pl.scale
            scale = (sx + sy + sz) / 3.0
            x, y, z = sh(pl.position)
            tf.append((x, y, z, _yaw_from_quat(pl.quat), scale))
            raws.append({"key": pl.key, "scale": list(pl.scale), "quat": list(pl.quat)})
        faces = [(f[1], f[2], f[3]) for f in m.faces if max(f[1:]) < len(m.vertices)]
        props.append(Prop(kind, m.name, m.material, bool(m.dynamic), m.shape, list(m.vertices), faces, tf, raws))
    return props


def clamp_to_line(stage):
    """STJ requires every s inside the reference line. A few stages store a note, or the stop, past its end (a note at
    99000 m, a stop 3.6 m beyond): clamp to the end and keep the stored value in x_rbr.clamped_s."""
    end = stage.line.chainage[-1]
    moved = []
    for n in stage.notes:
        if n["s"] > end:
            moved.append({"what": "pacenote", "stored": n["s"], "to": round(end, 3)})
            n["s"] = round(end, 3)
    tm = stage.timing
    for name in ("start", "finish", "stop"):
        v = getattr(tm, name)
        if v is not None and v > end:
            moved.append({"what": f"timing.{name}", "stored": round(v, 3), "to": round(end, 3)})
            setattr(tm, name, end)
    tm.splits = [x for x in tm.splits]
    if moved:
        stage.x_rbr["clamped_s"] = moved
        stage.lost.append(f"{len(moved)} s value(s) beyond the end of the driveline were clamped to it (stored values in x_rbr.clamped_s)")


def _load_physics(stage, folder, opt):
    """Grip, roughness and tyre tables from the install's Physics folder (plain text), when it can be found and read."""
    from ..readers import physics_lsp
    d = physics_lsp.find_dir(folder, opt.physics)
    stage.physics = physics_lsp.load(d) if d else None
    if stage.physics is None:
        stage.lost.append("no readable Physics/physics.lsp was found, so surfaces carry class and RBR id only (no grip numbers); "
                          "pass --physics <folder> or --surface-table")


def _finish(stage, ref):
    """Pace notes, direction diagnostics and the lost list common to every classic stage."""
    notes, skipped = convert_notes(stage.raw_notes)
    stage.x_rbr["notes_skipped"] = skipped
    stage.diagnostics["pacenotes"] = {"count": len(notes), "direction_vs_geometry": direction_check(notes, ref)}
    stage.notes = notes
    clamp_to_line(stage)
