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
    "Tyre and car physics, and how wetness and wear change grip: surfaces carry each tyre's absolute mu per surface group "
    "(from Physics/physics.lsp and tyres.lsp when the install is found) and the sinusoidal roughness, but WaterFactor, "
    "WetSlope, WearFtr, soil and rolling-resistance values stay in x_rbr (no documented formula; most are one default "
    "value); without the Physics folder surfaces carry class and RBR id only (--surface-table can supply numbers)",
    "NGP ruts and deformation state",
    "Shadow and specular maps and RBR shaders (baked vertex light is kept as displayColor, marked baked.vertex_colour)",
    "Lua character scripts and animations (script names are kept in x_rbr)",
    "Particle systems (the particle file name is kept in x_rbr)",
    "Visibility cells and containers",
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
                             "the textures are copied unchanged from the game and listed in assets[]."},
        "provenance": {
            "type": "converted",
            "detail": "Converted by rbr2stj from the stage files; surface classes are estimates from RBR surface names.",
            "confidence": "medium",
            "converted_from": [{"format": stage.kind, "tool": "rbr2stj", "tool_version": __version__,
                                "source": stage.source, "date": today, "lost": lost}]}}
    if stage.info.author:
        meta["author"] = stage.info.author
    return meta


# RBR tyre type (tyres.lsp TyreTypeNN) -> STJ tyre class. gravel_intermediate and gravel_wet have no honest STJ class
# (the gravel classes of STJ are dry gravel, snow, mud ...): their numbers stay in surfaces.x_rbr.tyre_types.
TYRE_CLASS = {0: "road", 1: "intermediate", 2: "wet", 3: "gravel", 6: "snow"}
TARMAC_GROUP = 2          # surface Type 2: the group STJ-REF (dry clean asphalt) belongs to


def _tyre_mu(physics: dict, group: int) -> dict:
    out = {}
    for t in physics.get("tyres", []):
        cls = TYRE_CLASS.get(t["index"])
        sf = t.get("SFric")
        if cls and sf and group < len(sf):
            out[cls] = sf[group]
    return out


def reference_mu(physics: dict | None) -> dict | None:
    """Each tyre class's absolute mu on the tarmac group = on STJ-REF (spec 6.5)."""
    if not physics:
        return None
    mu = _tyre_mu(physics, TARMAC_GROUP)
    return {cls: {"peak": v} for cls, v in mu.items()} or None


def _physics_entry(entry: dict, row: dict, physics: dict):
    """Grip and roughness of one surface from physics.lsp / tyres.lsp.

    Mapped: the grip group (`Type`) selects each tyre's `SFric_T<group>`, which is that tyre's absolute mu on the
    surface (`by_tyre_class.<class>.mu.peak`); `BumpAmplitude` / `BumpFrequency` become the sinusoidal roughness.
    Not mapped, kept raw in x_rbr: myStatic/myKinetic, SoilThickness, SinkFactor, SoilDensity and RollingResistance (the
    same default on 148 of 149 rows), WaterFactor and the tyres' WetSlope / WearFtr (the formula that combines them is
    not documented), CollMyHigh/Low and eHigh/eLow (collision friction and stiffness of solids)."""
    group = int(row.get("Type", 0))
    mus = _tyre_mu(physics, group)
    if mus:
        entry["by_tyre_class"] = {cls: {"mu": {"peak": v}} for cls, v in mus.items()}
    amp, freq = row.get("BumpAmplitude"), row.get("BumpFrequency")
    if isinstance(amp, (int, float)) and isinstance(freq, (int, float)) and amp > 0 and freq > 0:
        entry["roughness"] = {"sinusoid": {"amplitude": amp, "wavelength": round(1.0 / freq, 4)},
                              "_est": True, "_note": "BumpFrequency read as cycles per metre"}
    entry.pop("_est", None)
    entry["_source"] = (f"grip from {physics.get('source')}: surface group {group} ({_group_name(group)}), "
                        "each tyre's SFric_T<group>")
    entry.setdefault("x_rbr", {})["physics"] = {k: v for k, v in row.items() if k != "name"}


def _group_name(g: int) -> str:
    from ..readers import physics_lsp
    return physics_lsp.GROUPS.get(g, "?")


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
        row = (stage.physics or {}).get("surfaces", {}).get(sid)
        if row:
            _physics_entry(entry, row, stage.physics)
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
        if proto.get("collision") == "trunk":
            sx, sy, sz = _bbox_size(p)
            proto["collision_radius"] = _r(max(sx, sy) / 2, 3)
            proto["height"] = _r(sz, 3)
            proto["_note"] = "collision_radius and height are the horizontal half-extent and height of the collision mesh"
        if phys:
            proto["physics"] = phys
        protos[pid] = proto
        if p.transforms:
            tf = []
            for x, y, z, h, s in p.transforms:
                s = abs(s) or 1.0        # a negative scale (a mirrored placement) is invalid in STJ; the raw one is in x_rbr.placements
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
        return "tree", {"mode": "bendable", "mass": 50.0, "stiffness": 500.0, "_est": True,
                        "_note": "mass and stiffness are estimates: stage files carry neither"}, {}
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


def audio_section(stage: Stage) -> dict | None:
    """Sound spheres of the `.dls`: a position and a radius word, firing an event of the game's own sound banks. The
    event is not in the stage files, so each emitter has no `sound` and an `x_rbr` block (spec 8.6 'engine sound events')."""
    spheres = stage.x_rbr.get("sound_spheres") or []
    if not spheres:
        return None
    return {"emitters": [{"id": f"sound_sphere_{i + 1}", "kind": "other", "position": _vec(sp["position"]),
                          "max_distance": float(sp["radius_word"]), "loop": False,
                          "x_rbr": {"sound_sphere": i, "radius_word": sp["radius_word"],
                                    "note": "event name not stored in the stage files"}}
                         for i, sp in enumerate(spheres) if sp.get("radius_word", 0) > 0]}


_ANIMATED_KINDS = (("CLOCK", "clock"), ("FLAG", "flag"))


def animated_objects(stage: Stage, paths: dict) -> list[dict]:
    """Interactive objects (.lbs chunk 1,6) the game drives by script: start/finish clocks and flags. No keyframes are
    stored (spec 8.5 'engine-driven objects'): `kind` and the placed prims are given, the rest stays in `x_rbr`."""
    out = []
    for name, nodes in sorted(paths.items()):
        up = name.upper()
        kind = next((k for key, k in _ANIMATED_KINDS if key in up), None)
        if kind is None or not nodes:
            continue
        out.append({"id": slug(name, "object"), "name": name, "kind": kind,
                    "target": {"mesh_ref": "visual_extras", "nodes": nodes},
                    "x_rbr": {"interactive_object": name, "placements": len(nodes),
                              "note": "moved by the game's script; no keyframes are stored in the stage files"}})
    return out


def _rgb(low: dict, names) -> list[float] | None:
    try:
        return [round(float(low[n]), 4) for n in names] if all(n in low for n in names) else None
    except ValueError:
        return None


def _sun_vector(low: dict) -> list[float] | None:
    """RBR `SunDir`: the direction the light TRAVELS, in the stage (geometry) frame, Y up. STJ wants the unit vector from
    the ground towards the sun in world axes (x, y, z up): negate, then swap y and z like every geometry coordinate."""
    try:
        x, y, z = (float(v) for v in low["sundir"].replace(",", " ").split()[:3])
    except (KeyError, ValueError):
        return None
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-9:
        return None
    return [round(-x / n, 5), round(-z / n, 5), round(-y / n, 5)]


def weather_env(w, sky_note: bool = True) -> dict:
    """The part of `environment` that changes with the weather: sky (model, fog, engine keys) and lighting (a fixed sun
    direction and colour, ambient colour; spec 8.3 'a fixed lighting rig')."""
    s = w.settings
    low = {k.lower(): v for k, v in s.items()}
    sky: dict = {"model": "engine_default"}
    fog = _fog(s)
    if fog:
        sky["fog"] = {**fog, "_est": True,
                      "_note": f"RBR linear fog of weather {w.section}: visibility = FogEnd, colour = Fog RGB as stored"}
    sky["x_rbr"] = {k: s[k] for k in ("SkyboxName", "SkyboxSaturation", "Skybox_Scale", "CloudName", "Cloud_Scale",
                                      "Superbowl_Scale", "SuperbowlFogStart", "SuperbowlFogEnd", "Turbidity",
                                      "Rayleigh_Multiplier", "Mie_Multiplier", "Greenstein_Value", "Extinction",
                                      "Inscattering", "FogStart") if k in s}
    light: dict = {}
    sun: dict = {}
    d = _sun_vector(low)
    if d:
        sun["direction"] = d
    dc = _rgb(low, ("diffusered", "diffusegreen", "diffuseblue"))
    if dc:
        sun["color"] = dc
    if sun:
        light["sun"] = sun
    ac = _rgb(low, ("ambientred", "ambientgreen", "ambientblue"))
    if ac:
        light["ambient"] = {"color": ac, "intensity": 1.0}
    rest = {k: s[k] for k in s if k.lower().startswith(("car_", "character_", "particle_", "specular", "terrain_reflectance",
                                                          "sun_", "sunoffset"))}
    if rest:
        light["x_rbr"] = rest
    if light:
        light["_note"] = ("RBR TrackSettings: SunDir (fixed sun, converted to STJ axes), Diffuse RGB as the sun colour, Ambient "
                          "RGB as ambient; the values are the game's own, not converted from its colour space")
    out = {"sky": sky}
    if light:
        out["lighting"] = light
    return out


def environment_section(stage: Stage, base_letter: str | None, variants: list[str], wx_ids: dict | None = None) -> dict | None:
    """`wx_ids` maps a weather section to the id of the weather variant that patches the base's sky and lighting."""
    env: dict = {}
    wx_ids = wx_ids or {}
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
               "x_rbr": {"section": w.section, "settings": w.settings}}
        fog = _fog(w.settings)
        if fog:
            row["weather"]["visibility"] = fog["visibility"]
            row["weather"]["_note"] = "visibility = RBR FogEnd (linear fog: fully fogged there); an estimate"
            row["weather"]["_est"] = True
        if w.section in wx_ids:
            row["variant"] = wx_ids[w.section]
        elif w.letter != base_letter:
            row["variant"] = variant_id(w.letter)
        opts.append(row)
    if opts:
        env["weather_options"] = opts
    first = base_weather(stage, base_letter)
    if first is not None:
        env.update(weather_env(first))
    return env or None


def base_weather(stage: Stage, base_letter: str | None):
    return next((w for w in stage.weathers if w.letter == base_letter), None) or (stage.weathers[0] if stage.weathers else None)


def _fog(settings: dict) -> dict | None:
    """STJ `sky.fog` (visibility m, linear RGB) from RBR's TrackSettings fog keys; None when fog is off or absent."""
    low = {k.lower(): v for k, v in settings.items()}
    try:
        if low.get("usefog", "true").strip().lower() == "false" or "fogend" not in low:
            return None
        end = float(low["fogend"])
        if end <= 0:
            return None
        out = {"visibility": round(end, 1)}
        if all(k in low for k in ("fogred", "foggreen", "fogblue")):
            out["color"] = [round(float(low[k]), 4) for k in ("fogred", "foggreen", "fogblue")]
        return out
    except ValueError:
        return None


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
                   variants: list[dict] | None = None, base_letter: str | None = None, source_files=(),
                   wx_ids: dict | None = None) -> dict:
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
        cl["tangents_out"] = [_vec(t) for t in line.tangents]
        if line.tangents_in:
            cl["tangents_in"] = [_vec(t) for t in line.tangents_in]
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
        rm = reference_mu(stage.physics)
        if rm:
            surfaces["reference_mu"] = rm
            surfaces["x_rbr"] = {"tyre_types": stage.physics["tyres"], "physics_source": stage.physics["source"]}
        surfaces["maps"] = surface_maps(stage, names, "collision", "Ground")
    if stage.props and "props" in mesh_files:
        uri, prims = mesh_files["props"]
        geometry_meshes.append({"id": "props", "uri": uri, "roles": ["collision"]})
        protos, inst, _ids = prototypes_and_instances(stage, prims, "props")
        doc["scenery"] = {"prototypes": protos, "instances": inst}
    veg = mesh_files.get("vegetation")
    if veg:
        sc = doc.setdefault("scenery", {"prototypes": {}, "instances": []})
        used: set = set(sc["prototypes"])
        for v in veg:
            pid, k = slug(v["name"], "veg"), 2
            base_id = pid
            while pid in used:
                pid, k = f"{base_id}_{k}", k + 1
            used.add(pid)
            sc["prototypes"][pid] = {"name": v["name"], "class": v["class"], "collision": "none",
                                     "mesh": {"mesh_ref": "vegetation", "prim": v["proto"]},
                                     "x_btb": {"object": v["index"], "material": v["material"]}}
            sc["instances"].append({"prototype": pid, "usd": {"mesh_ref": "vegetation", "prim": v["instancer"]},
                                    "_note": f"{v['count']} placements of BTB object {v['index']}, class from the material name"})
    for extra in mesh_files.get("extra", []):
        geometry_meshes.append(extra)
    if geometry_meshes:
        doc["geometry"] = {"meshes": geometry_meshes}
    if surfaces:
        doc["surfaces"] = surfaces
    if stage.edges:
        doc["edges"] = stage.edges
    water = water_section(stage)
    if mesh_files.get("visual_water"):
        water = water or {"bodies": []}
        water["bodies"].append({"id": "visual_water", "kind": "other", "physical": False,
                                "mesh": {"mesh_ref": mesh_files["visual_water"]},
                                "_note": "RBR water faces (.lbs chunk 1,8): drawn only; they carry no depth or physics"})
    if water:
        doc["water"] = water
    audio = audio_section(stage)
    if audio:
        doc["audio"] = audio
    anim = animated_objects(stage, mesh_files.get("interactive_paths") or {})
    if anim:
        doc["animated_objects"] = anim
    if mesh_files.get("assets"):
        doc["assets"] = mesh_files["assets"]
    env = environment_section(stage, base_letter, [v["letter"] for v in (variants or []) if "letter" in v], wx_ids)
    if env:
        doc["environment"] = env
    if variants:
        doc["variants"] = [({"id": v["id"], "name": v["name"], "kind": "weather", "override": v["override"]} if "id" in v else
                            {"id": variant_id(v["letter"]), "name": VARIANT_NAMES.get(v["letter"], v["letter"]),
                             "kind": VARIANT_KIND.get(v["letter"], "other"), "override": v["override"]})
                           for v in variants]
    if stage.x_btb:
        doc["x_btb"] = stage.x_btb
    x = dict(stage.x_rbr)
    x["diagnostics"] = stage.diagnostics
    doc["x_rbr"] = x
    return doc
