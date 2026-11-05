"""IR -> STJ document (a dict), with provenance, the `lost` list and `x_rbr`.

The mapping tables live in docs/mapping.md; the rules of the document are the STJ spec v0.3.
"""

from __future__ import annotations

import datetime as dt
import math
import re

from .. import __version__
from ..ir import Stage
from ..readers import surfaces_table

VERSION = "0.3"

# What every conversion drops (PLAN.md §8). Format-specific lines are added per stage.
LOST_ALWAYS = [
    "Grip and all tyre physics: they live in the NGP/physics.lsp data, not in the stage files, so surface "
    "`mf_scaling`, friction and deformation numbers are absent (surfaces carry class and RBR id only; "
    "--surface-table can supply numbers)",
    "NGP ruts and deformation state",
    "Baked vertex light, shadow maps and RBR shaders",
    "Lua character scripts and animations (script names are kept in x_rbr)",
    "Particle systems (the particle file name is kept in x_rbr)",
    "Visibility cells and near/far/always draw lists",
]

TIME_OF_DAY = {"M": "09:00", "E": "18:00", "N": "22:00", "O": "12:00"}
VARIANT_NAMES = {"M": "Morning", "N": "Night", "E": "Evening", "O": "Overcast", "S": "Variant S"}
VARIANT_KIND = {"M": "time_of_day", "N": "time_of_day", "E": "time_of_day", "O": "weather", "S": "other"}


def slug(text: str, fallback: str = "stage") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or fallback


def variant_id(letter: str) -> str:
    return f"variant_{letter.lower()}"


def weather_condition(preset: str, cloud: str) -> str:
    if preset == "lightrain":
        return "rain"
    if preset == "heavyrain":
        return "heavy_rain"
    if preset in ("lightsnow", "heavysnow"):
        return "snow"
    if preset in ("lightfog", "heavyfog"):
        return "fog"
    return {"clear": "clear", "partcloud": "partly_cloudy", "lightcloud": "cloudy", "heavycloud": "overcast"}[cloud]


def _r(v, n=3):
    return round(float(v), n)


def _vec(p, n=3):
    return [_r(c, n) for c in p]


def metadata(stage: Stage, extra_lost: list[str], source_files: list[str]) -> dict:
    is_btb = stage.kind == "rbr-btb"
    today = dt.date.today().isoformat()
    lost = LOST_ALWAYS + list(stage.lost) + extra_lost
    meta = {
        "specification": "STJ", "version": VERSION, "coordinate_system": "OpenDRIVE", "units": "SI",
        "description": f"{stage.info.name}: Richard Burns Rally {'BTB ' if is_btb else ''}stage converted by rbr2stj.",
        "created": today,
        "source_format": "RBR/BTB" if is_btb else "RBR",
        "licence": {"redistributable": False,
                    "notes": "Converted from Richard Burns Rally content the user owns. Not for redistribution; "
                             "textures and scenery assets are referenced by name only, never embedded."},
        "provenance": {
            "type": "converted",
            "detail": "Converted by rbr2stj from the stage files; surface classes are estimates from RBR surface names.",
            "confidence": "medium",
            "converted_from": [{"format": stage.kind, "tool": "rbr2stj", "tool_version": __version__,
                                "source": stage.source, "date": today, "lost": lost}]}}
    if stage.info.author:
        meta["author"] = stage.info.author
    return meta


def _surface_library(stage: Stage, user_table: dict | None) -> dict:
    ids = set()
    if stage.collision:
        for st in stage.collision.states:
            ids.update(st.palette)
    lib = {}
    for sid in sorted(ids):
        name = surfaces_table.name(sid)
        cls, resp = surfaces_table.classify(sid)
        entry = {"name": name.replace("_", " "), "class": cls, "_est": True,
                 "_source": "class guessed from the RBR surface name; no grip numbers (see provenance.lost)",
                 "x_rbr": {"id": sid, "name": name}}
        if resp:
            entry["impact"] = {"response": resp}
        if user_table:
            ov = user_table.get(str(sid)) or user_table.get(name) or user_table.get(name.lower())
            if ov:
                entry.update(ov)
        lib[surfaces_table.stj_id(sid)] = entry
    return lib


def _condition_range(signal: str, lo: float | None, hi: float | None) -> dict:
    conds = []
    if lo is not None:
        conds.append({"signal": signal, "op": "ge", "value": lo})
    if hi is not None:
        conds.append({"signal": signal, "op": "lt", "value": hi})
    return conds[0] if len(conds) == 1 else {"all": conds}


WEAR_RANGE = {"new": (None, 0.25), "normal": (0.25, 0.75), "worn": (0.75, None)}
WET_RANGE = {"dry": (None, 0.25), "damp": (0.25, 0.75), "wet": (0.75, None)}


def _state_when(wear: str, wet: str) -> dict:
    return {"all": [_condition_range("wear", *WEAR_RANGE[wear]), _condition_range("wetness", *WET_RANGE[wet])]}


def surface_maps(stage: Stage, names: dict, mesh_id: str, material: str) -> list[dict]:
    maps = []
    states = stage.collision.states
    single = len(states) == 1 and len(states[0].merged) in (0, 1, 9)
    for st in states:
        lp, wp = names[st.key]
        m = {"mesh_ref": mesh_id, "material": material,
             "palette": [surfaces_table.stj_id(s) for s in st.palette],
             "weights": {"encoding": "vertex_top3", "weights": wp, "indices": [f"{lp}_{q}" for q in range(3)]},
             "contact": "blend"}
        merged = st.merged or [(st.wear, st.wetness)]
        if len(states) == 1 and len(merged) == 9:
            pass                                    # every state is identical: one map, always on
        elif len(states) == 1 and len(merged) == 1 and stage.kind == "rbr-btb":
            pass
        else:
            conds = [_state_when(w, e) for w, e in merged]
            m["when"] = conds[0] if len(conds) == 1 else {"any": conds}
            m["_note"] = "RBR surface state: " + ", ".join(f"{w}/{e}" for w, e in merged)
        maps.append(m)
    return maps


def prototypes_and_instances(stage: Stage, prim_paths: dict[str, str], mesh_id: str):
    """(prototypes dict, instances list, {prop index: id}) from stage.props."""
    protos, instances, ids = {}, [], {}
    used: set[str] = set()
    for i, p in enumerate(stage.props):
        base = slug(p.name.rsplit(".", 1)[0] if "." in p.name else p.name, f"prop_{i}")
        pid, k = base, 2
        while pid in used:
            pid, k = f"{base}_{k}", k + 1
        used.add(pid)
        ids[i] = pid
        cls, phys, extra = prop_class(p)
        proto = {"name": p.name, "class": cls, "mesh": {"mesh_ref": mesh_id, "prim": prim_paths[pid]},
                 "x_rbr": {"kind": p.kind, "material": p.material, "surface": surfaces_table.name(p.material),
                           "dynamic": p.dynamic, **({"shape": _shape_json(p.shape)} if p.shape else {})}}
        proto.update(extra)
        if phys:
            proto["physics"] = phys
        protos[pid] = proto
        if p.transforms:
            tf = []
            for x, y, z, h, s in p.transforms:
                row = [_r(x), _r(y), _r(z), _r(h, 4)]
                if abs(s - 1.0) > 1e-4:
                    row.append(_r(s, 4))
                tf.append(row)
            instances.append({"prototype": pid, "transforms": tf, "x_rbr": {"placements": p.placements_raw}})
    return protos, instances, ids


def _shape_json(shape):
    if shape[0] == "box":
        return {"box": {"min": _vec(shape[1]), "max": _vec(shape[2])}}
    return {"sphere": {"centre": _vec(shape[1]), "radius": _r(shape[2])}}


def _bbox_size(p):
    if p.shape and p.shape[0] == "box":
        size = [abs(b - a) for a, b in zip(p.shape[1], p.shape[2])]
    else:
        xs = list(zip(*p.vertices)) if p.vertices else [(0,), (0,), (0,)]
        size = [max(c) - min(c) for c in xs]
    return [max(round(s, 3), 0.05) for s in size]


# kind -> (STJ class, physics mode, mass kg) ; masses are estimates (the stage files hold no mass)
KIND_CLASS = {"cone": ("prop", "movable", 1.5), "block": ("prop", "movable", 25.0), "sign": ("sign", None, None),
              "banner": ("sign", None, None), "log": ("prop", "movable", 40.0), "log_pole": ("pole", None, None),
              "fence_bar": ("fence", None, None), "fence_pole": ("fence", None, None)}


def prop_class(p):
    """(class, physics dict or None, extra prototype keys) for a `.trk` mesh."""
    if p.kind in KIND_CLASS and p.kind != "static":
        cls, mode, mass = KIND_CLASS[p.kind]
        extra = {"collision": "mesh"}
        if p.kind in ("sign", "banner"):
            extra["breakable"] = True
        if mode:
            phys = {"mode": mode, "mass": mass, "shape": {"kind": "box", "size": _bbox_size(p)}, "_est": True,
                    "_note": "mass is an estimate: stage files carry no mass"}
            return cls, phys, {}
        return cls, None, extra
    # static (kind 0 and unknown kinds): the surface id says what it is (bush, tree, rock, pole ...)
    n = surfaces_table.name(p.material).lower()
    extra = {"collision": "mesh"}
    if n.startswith("bendable_tree"):
        return "tree", {"mode": "bendable", "stiffness": 500.0, "_est": True,
                        "_note": "stiffness is an estimate"}, {}
    if "bush" in n:
        return "bush", None, extra
    if n.startswith(("tree", "trunk")):
        return "tree", None, {"collision": "trunk"}
    if n.startswith("rock"):
        return "rock", None, extra
    if n.startswith("metal_pole"):
        return "pole", None, extra
    return "prop", None, extra


def _ford_path(w):
    """A ford as a path body: along the quad's longer axis (the stream runs across the road), width = the short side.
    RBR carries no depth; 0.15 m is a marked estimate."""
    pts = w.outline
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts[0], pts[1], pts[2], pts[3]
    e1 = math.hypot(x1 - x0, y1 - y0)
    e2 = math.hypot(x2 - x1, y2 - y1)
    if e1 >= e2:      # long edges are 0-1 and 2-3: the path joins the midpoints of the short edges 3-0 and 1-2
        a, b, width = ((x3 + x0) / 2, (y3 + y0) / 2), ((x1 + x2) / 2, (y1 + y2) / 2), e2
    else:
        a, b, width = ((x0 + x1) / 2, (y0 + y1) / 2), ((x2 + x3) / 2, (y2 + y3) / 2), e1
    return [[_r(a[0]), _r(a[1]), _r(w.level), _r(width), 0.15], [_r(b[0]), _r(b[1]), _r(w.level), _r(width), 0.15]]


def water_section(stage: Stage) -> dict | None:
    if not stage.water:
        return None
    bodies = []
    counts: dict[str, int] = {}
    for w in stage.water:
        i = counts[w.kind] = counts.get(w.kind, 0) + 1
        b = {"id": f"{w.kind}_{i}", "kind": w.kind}
        if w.kind == "ford":
            b["path"] = _ford_path(w)
            b["drivable"] = True
            b["_note"] = "RBR deep-water quad across the road; depth 0.15 m is an estimate (stage files carry none)"
            b["x_rbr"] = {"outline": [[_r(x), _r(y)] for x, y in w.outline]}
        else:
            b["outline"] = [[_r(x), _r(y)] for x, y in w.outline]
            b["level"] = _r(w.level)
        if w.when_wet:
            b["when"] = {"signal": "wetness", "op": "ge", "value": 0.5}
            b["_note"] = "RBR wet puddle quad: exists only when the stage is wet"
        bodies.append(b)
    return {"bodies": bodies}


def environment_section(stage: Stage, base_letter: str | None, variants: list[str]) -> dict | None:
    env: dict = {}
    if stage.origin_offset:
        env["origin_offset"] = _vec(stage.origin_offset)
    letter = stage.info.letter
    if letter and letter in TIME_OF_DAY:
        env["default_time"] = {"local_time": TIME_OF_DAY[letter],
                               "_note": f"estimate from the RBR variant letter {letter}"}
    if stage.info.default_wetness in (1, 2):
        depth = 0.0003 if stage.info.default_wetness == 1 else 0.001
        env["default_weather"] = {"water_depth": depth, "_est": True,
                                  "_note": "from RSF DefaultWetness (damp/wet); the depth is an estimate"}
    opts = []
    present = {base_letter, *variants}
    for w in stage.weathers:
        if w.letter not in present:
            continue                       # a stage letter that is not in this package: kept in x_rbr only
        row = {"weather": {"condition": weather_condition(w.preset, w.cloud)}, "weight": 1.0,
               "x_rbr": {"section": w.section}}
        if w.letter != base_letter:
            row["variant"] = variant_id(w.letter)
        opts.append(row)
    if opts:
        env["weather_options"] = opts
    return env or None


def track_info(stage: Stage) -> dict:
    i = stage.info
    ti = {"name": i.name, "type": "point_to_point"}
    if i.surface in ("tarmac", "gravel", "snow"):
        ti["primary_surface"] = i.surface
    if i.length_m:
        ti["length"] = _r(i.length_m, 1)
    if i.country:
        ti["country"] = i.country[:2].upper() if len(i.country) >= 2 else i.country
    if i.comment:
        ti["description"] = i.comment
    return ti


def build_document(stage: Stage, *, mesh_files: dict, user_table: dict | None = None, extra_lost: list[str] | None = None,
                   variants: list[dict] | None = None, base_letter: str | None = None, source_files=()) -> dict:
    """mesh_files: {"collision": (uri, {state key: (layers, weights)}), "props": (uri, {pid: prim}), ...}"""
    extra_lost = list(extra_lost or [])
    line = stage.line
    doc: dict = {"_metadata": metadata(stage, extra_lost, list(source_files))}
    doc["track_info"] = track_info(stage)
    cl = {"points": [_vec(p) for p in line.points], "closed": False,
          "width": {"left": 3.5, "right": 3.5, "_est": True,
                    "_note": "RBR stages carry no road width; 3.5 m per side is a placeholder"},
          "chainage": [_r(c) for c in line.chainage]}
    if line.tangents:
        cl["tangents"] = [_vec(t) for t in line.tangents]
    doc["reference"] = {"centerline": cl}

    tm = stage.timing
    timing: dict = {}
    if tm.start is not None:
        timing["start"] = {"s": _r(tm.start)}
    if tm.splits:
        timing["split_points"] = [{"id": f"split{i + 1}", "s": _r(s)} for i, s in enumerate(tm.splits)]
    if tm.finish is not None:
        timing["finish"] = {"s": _r(tm.finish)}
    if tm.stop is not None:
        timing["stop"] = {"s": _r(tm.stop)}
    if timing:
        doc["timing"] = timing

    race: dict = {}
    if stage.notes:
        race["pacenotes"] = {"scale": "ascending_1_6", "language": "en", "notes": stage.notes}
    if stage.spawn:
        race["spawns"] = [{"id": "start", "pose": {"position": _vec(stage.spawn.position),
                                                   "heading": _r(stage.spawn.heading, 4)},
                           "_note": "RBR start position; STJ poses place the SVJ origin (front axle midpoint)"}]
    if race:
        doc["race"] = race

    geometry_meshes = []
    surfaces: dict = {}
    if stage.collision is not None and "collision" in mesh_files:
        uri, names = mesh_files["collision"]
        geometry_meshes.append({"id": "collision", "uri": uri, "roles": ["collision"]})
        surfaces["library"] = _surface_library(stage, user_table)
        surfaces["maps"] = surface_maps(stage, names, "collision", "Ground")
    if stage.props and "props" in mesh_files:
        uri, prims = mesh_files["props"]
        geometry_meshes.append({"id": "props", "uri": uri, "roles": ["collision"]})
        protos, inst, _ids = prototypes_and_instances(stage, prims, "props")
        doc["scenery"] = {"prototypes": protos, "instances": inst}
    for extra in mesh_files.get("extra", []):
        geometry_meshes.append(extra)
    if geometry_meshes:
        doc["geometry"] = {"meshes": geometry_meshes}
    if surfaces:
        doc["surfaces"] = surfaces
    if stage.edges:
        doc["edges"] = stage.edges
    water = water_section(stage)
    if water:
        doc["water"] = water
    env = environment_section(stage, base_letter, [v["letter"] for v in (variants or [])])
    if env:
        doc["environment"] = env
    if variants:
        doc["variants"] = [{"id": variant_id(v["letter"]), "name": VARIANT_NAMES.get(v["letter"], v["letter"]),
                            "kind": VARIANT_KIND.get(v["letter"], "other"), "override": v["override"]}
                           for v in variants]
    if stage.x_btb:
        doc["x_btb"] = stage.x_btb
    x = dict(stage.x_rbr)
    x["diagnostics"] = stage.diagnostics
    doc["x_rbr"] = x
    return doc
