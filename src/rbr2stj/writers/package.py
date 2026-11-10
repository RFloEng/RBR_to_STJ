"""Write an STJ folder package: root `.usda` (STJ document embedded in `customLayerData["stj"]`, spec §7.8), mesh
layers, variant layers and an extracted `<name>.stj.json` for diffing. Optionally a `.usdz` through STJ's own tool.

The STJ tools (`stj_core`, `stj_usd`, `validate`) are used when found (env STJ_TOOLS, `reference/STJ/tools`, or a
sibling `STJ-Standard-Track-Json/tools`); the embedding itself is the 6-line recipe the spec defines, so a package can
be written without them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..convert import Options
from ..errors import Rbr2StjError
from ..ir import Stage
from .. import textures
from . import lbs_extras
from . import lbs_visual, stj_doc, usd_mesh
from .stj_doc import slug

FACE_LIMIT_USDA = 50_000


def find_stj_tools() -> Path | None:
    cands = []
    if os.environ.get("STJ_TOOLS"):
        cands.append(Path(os.environ["STJ_TOOLS"]))
    here = Path(__file__).resolve()
    for up in (here.parents[3], here.parents[4] if len(here.parents) > 4 else here.parents[3], Path.cwd()):
        cands += [up.parent / "STJ repo" / "tools", up / "reference" / "STJ" / "tools", up.parent / "STJ-Standard-Track-Json" / "tools"]
    for c in cands:
        if (c / "validate.py").is_file():
            return c
    return None


def merge_diff(base, other):
    """RFC 7396 merge patch that turns `base` into `other`: {} when equal; None marks a deleted key."""
    if not isinstance(base, dict) or not isinstance(other, dict):
        return other
    patch = {}
    for k in base:
        if k not in other:
            patch[k] = None
    for k, v in other.items():
        if k not in base:
            patch[k] = v
        elif base[k] != v:
            patch[k] = merge_diff(base[k], v) if isinstance(base[k], dict) and isinstance(v, dict) else v
    return patch


def embed(doc: dict, layer_path: Path):
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    data = dict(layer.customLayerData or {})
    data["stj"] = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    layer.customLayerData = data
    layer.Save()


@dataclass
class Package:
    folder: Path
    root: Path
    json_path: Path
    files: list[Path] = field(default_factory=list)
    usdz: Path | None = None
    validation: tuple[int, str] | None = None
    blend: tuple[int, str, Path | None] | None = None


def _baked(info: dict) -> dict:
    """`baked` of a classic visual layer: the vertex light, and the game's shadow and specular maps. A material carries
    one map of each (a render chunk names its own), so the entry lists the first and all of them in `_uris`."""
    out = {"vertex_colour": {"primvar": "displayColor", "role": "light"}}
    for key, label, tc in (("shadow_maps", "shadow_map", "st_shadow"), ("specular_maps", "specular_map", "st_specular")):
        uris = info.get(key) or []
        if uris:
            out[label] = {"uri": uris[0], "texcoord": tc,
                          "_uris": uris, "_note": "one map per render chunk: each material names its own (stj:shadowMap / "
                                                  "stj:specularMap); the uri is the first of _uris"}
    return out


def _mesh_ext(opt: Options, faces: int) -> str:
    if opt.mesh_format in ("usda", "usdc"):
        return opt.mesh_format
    return "usda" if faces <= FACE_LIMIT_USDA else "usdc"


def _same(a: Stage, b: Stage, *exts: str) -> bool:
    return all(a.file_hashes.get(k) is not None and a.file_hashes.get(k) == b.file_hashes.get(k) for k in exts)


def _stem(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def _chosen_sky(stage: Stage, fname: str, items):
    """Names of the dome meshes the first weather selects (`CloudName` for Clouds.lbs, `SkyboxName` for skyboxes.lbs),
    or None to show everything when no weather names one."""
    if not stage.weathers:
        return None
    key = "cloudname" if fname.lower().startswith("clouds") else "skyboxname"
    want = next((v for k, v in stage.weathers[0].settings.items() if k.lower() == key), None)
    if not want:
        return None
    stems = {_stem(i.name) for i in items}
    w = want.lower()
    best = max((s for s in stems if w == s or w.startswith(s)), key=len, default=None)
    return {best} if best else None


_VEG = (("tree", re.compile(r"vege|tree|arbol|birch|pine|oak|encina|eucal|poplar|palm", re.I)),
        ("bush", re.compile(r"bush|arbust|shrub|hedge", re.I)),
        ("grass", re.compile(r"grass|hierba|pasto|cesped|weed|fern|flower", re.I)))


def _first_material(item) -> str:
    names = item["mesh"].material_names
    return names[0] if names else ""


def _vegetation_class(item, track) -> str | None:
    """STJ prototype class (tree / bush / grass) from the material and texture names of a BTB object, or None.
    Names are all BTB carries: there is no vegetation flag in the format."""
    name = _first_material(item)
    if not name:
        return None
    sec = track.materials.get(name.lower()) if track is not None else None
    text = f"{name} {getattr(sec, 'diffuse', '') or ''}"
    for cls, rx in _VEG:
        if rx.search(text):
            return cls
    return None


def _asset_manifest(folder: Path, rels: list[str], stage: Stage) -> list[dict]:
    """`assets[]` (spec 5.3): every texture copied into the package, with its hash and the licence it came with."""
    import hashlib
    src = "Richard Burns Rally / RallySimFans stage content" if stage.kind == "rbr-classic" else "BTB track archive"
    out = []
    for rel in rels:
        f = folder / rel
        if not f.is_file():
            continue
        out.append({"id": stj_doc.slug(Path(rel).stem, "texture"), "uri": rel, "role": "texture",
                    "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                    "licence": {"redistributable": False, "notes": "Copied unchanged from content the user owns; not for redistribution."},
                    "source": src})
    seen: dict = {}
    for a in out:                              # ids must be unique
        n = seen[a["id"]] = seen.get(a["id"], 0) + 1
        if n > 1:
            a["id"] = f"{a['id']}_{n}"
    return out


def _write_meshes(stage: Stage, folder: Path, stem: str, opt: Options, base: tuple[Stage, dict] | None = None):
    """Write the stage's mesh layers (reusing the base stage's when the source files are byte-identical, which
    answers PLAN open question 7 per stage); returns (`mesh_files` for build_document, layers written)."""
    mesh_files: dict = {}
    written: list[Path] = []
    exporter = (textures.TextureExporter(stage.texture_source, folder / 'textures')
                if stage.texture_source is not None and opt.textures else None)
    if stage.collision is not None:
        if base and _same(stage, base[0], "col", "mat") and "collision" in base[1]:
            mesh_files["collision"] = base[1]["collision"]
        else:
            ext = _mesh_ext(opt, len(stage.collision.faces))
            rel = f"meshes/{stem}_collision.{ext}"
            names = usd_mesh.write_collision(folder / rel, stage.collision)
            mesh_files["collision"] = (rel, names)
            written.append(folder / rel)
    if stage.props:
        if base and _same(stage, base[0], "trk") and "props" in base[1]:
            mesh_files["props"] = base[1]["props"]
        else:
            rel = f"meshes/{stem}_props.usda"
            props = {}
            used: set[str] = set()     # prototype ids are derived exactly as stj_doc.prototypes_and_instances does
            for i, p in enumerate(stage.props):
                b0 = slug(p.name.rsplit(".", 1)[0] if "." in p.name else p.name, f"prop_{i}")
                pid, k = b0, 2
                while pid in used:
                    pid, k = f"{b0}_{k}", k + 1
                used.add(pid)
                props[pid] = p
            prims = usd_mesh.write_props(folder / rel, props)
            mesh_files["props"] = (rel, prims)
            written.append(folder / rel)
    if stage.visual:
        veg, rest = [], []
        for it in stage.visual:
            cls = _vegetation_class(it, stage.btb_track)
            (veg if cls and len(it["matrices"]) > 1 else rest).append((it, cls))
        rel = f"meshes/{stem}_visual.usda"
        info = usd_mesh.write_visual(folder / rel, [i for i, _c in rest], stage.btb_track, stage.shift, exporter)
        info.pop("objects", None)
        stage.diagnostics["visual"] = info
        mesh_files.setdefault("extra", []).append({"id": "visual", "uri": rel, "roles": ["visual"]})
        written.append(folder / rel)
        if veg:
            rel = f"meshes/{stem}_vegetation.usda"
            vinfo = usd_mesh.write_visual(folder / rel, [i for i, _c in veg], stage.btb_track, stage.shift, exporter)
            objs = vinfo.pop("objects")
            stage.diagnostics["vegetation"] = vinfo
            mesh_files.setdefault("extra", []).append({"id": "vegetation", "uri": rel, "roles": ["visual"]})
            written.append(folder / rel)
            mesh_files["vegetation"] = [{"class": cls, "name": it["object"].file, "index": it["object"].index,
                                         "material": _first_material(it), **objs[it["object"].index]}
                                        for it, cls in veg if it["object"].index in objs]
    if stage.lbs_visual:
        shared = []
        if base and _same(stage, base[0], "lbs", "ini"):
            shared = [e for e in base[1].get("extra", []) if e["id"] in ("visual", "visual_lod1")]
        if shared:
            mesh_files.setdefault("extra", []).extend(shared)
            if base[0].x_rbr.get("texture_files") is not None:
                stage.x_rbr["texture_files"] = base[0].x_rbr["texture_files"]
        else:
            tris = sum(c.triangles for b in stage.lbs_visual for c in b.chunks if c.draw_list != 3)
            rel = f"meshes/{stem}_visual.{_mesh_ext(opt, tris)}"
            info = lbs_visual.write_lbs_visual(folder / rel, stage.lbs_visual, stage.texture_requests, stage.shift, exporter,
                                               shadow_requests=stage.shadow_requests, specular_requests=stage.specular_requests)
            stage.diagnostics["visual"] = info
            # vertex colour is the game's baked light: said so (spec 7.2 `baked`), never multiplied into the materials
            mesh_files.setdefault("extra", []).append({
                "id": "visual", "uri": rel, "roles": ["visual"],
                "baked": _baked(info)})
            written.append(folder / rel)
            far_tris = sum(c.triangles for b in stage.lbs_visual for c in b.chunks if c.draw_list == 3)
            if far_tris:
                rel = f"meshes/{stem}_visual_lod1.{_mesh_ext(opt, far_tris)}"
                stage.diagnostics["visual_lod1"] = lbs_visual.write_lbs_visual(
                    folder / rel, stage.lbs_visual, stage.texture_requests, stage.shift, exporter, far=True,
                    shadow_requests=stage.shadow_requests, specular_requests=stage.specular_requests)
                mesh_files["extra"].append({
                    "id": "visual_lod1", "uri": rel, "roles": ["lod"],
                    "lod": {"of": "visual", "level": 1, "switch_distance": 600.0,
                            "_est": True, "_note": "RBR draw list 3 (far); the switch distance is the ~600 m the game's "
                                                   "renderer is documented to use, not a stored value"},
                    "baked": _baked(stage.diagnostics["visual_lod1"])})
                written.append(folder / rel)
    if stage.lbs_extras:
        groups = [("visual_extras", {"lanes", "interactive", "clouds", "sky"}, {}),
                  ("visual_reflection", {"reflection"}, {"reflective": True}),
                  ("visual_water", {"water"}, {})]
        for mid, names, flags in groups:
            items = [i for i in stage.lbs_extras if i.group in names]
            if not items:
                continue
            rel = f"meshes/{stem}_{mid.split('visual_')[-1] if mid != 'visual_extras' else 'extras'}.{_mesh_ext(opt, sum(len(i.indices) // 3 for i in items))}"
            info = lbs_extras.write_extras(folder / rel, items, stage.texture_requests, stage.shift, exporter)
            paths = info.pop("placement_paths", {})
            stage.diagnostics.setdefault("visual_extras", {})[mid] = info
            mesh_files.setdefault("extra", []).append({"id": mid, "uri": rel, "roles": ["visual"], **flags})
            written.append(folder / rel)
            if mid == "visual_extras":
                mesh_files["interactive_paths"] = paths
                if info.get("wind"):
                    mesh_files["wind"] = {**info["wind"], "mesh_ref": mid}
            if mid == "visual_water":
                mesh_files["visual_water"] = mid
    for fname, tex, items in stage.sky_files:
        rel = f"meshes/{stem}_{fname.rsplit('.', 1)[0].lower()}.{_mesh_ext(opt, sum(len(i.indices) // 3 for i in items))}"
        sky_exp = (textures.TextureExporter(stage.texture_source, folder / 'textures')
                   if exporter is not None else None)
        if sky_exp is not None:
            sky_exp.done, sky_exp._names = exporter.done, exporter._names      # one shared texture folder and cache
            sky_exp.failed = exporter.failed
        chosen = _chosen_sky(stage, fname, items)
        stage.diagnostics.setdefault("sky_files", {})[fname] = lbs_extras.write_extras(
            folder / rel, items, tex, (0.0, 0.0), sky_exp,
            visible=lambda it, c=chosen: c is None or _stem(it.name) in c)
        mesh_files.setdefault("extra", []).append({"id": rel.rsplit('/', 1)[-1].rsplit('.', 1)[0], "uri": rel,
                                                    "roles": ["visual"]})
        written.append(folder / rel)
    if exporter is not None and exporter.done:
        stage.diagnostics["textures"] = exporter.summary()
        stage.x_rbr["texture_files"] = {k: v[0] for k, v in exporter.done.items() if v}
        mesh_files["assets"] = _asset_manifest(folder, sorted({v[0] for v in exporter.done.values() if v}), stage)
    return mesh_files, written


def _root_layer(path: Path, mesh_rels: list[str], name: str):
    """The root layer: a /track Xform that references the mesh layers, so the stage opens in any USD viewer."""
    from pxr import UsdGeom
    stage = usd_mesh.new_stage(path, "track")
    for rel in mesh_rels:
        prim = stage.DefinePrim(f"/track/{Path(rel).stem}", "Xform")
        prim.GetReferences().AddReference("./" + rel)
    stage.GetRootLayer().documentation = f"STJ track package for {name}. The STJ document is in customLayerData['stj']."
    stage.GetRootLayer().Save()


def write_package(stages: list[Stage], out: Path, opt: Options | None = None, user_table: dict | None = None,
                  usdz: bool = False, validate: bool = False,
                  blend: bool = False, blend_background: bool = False) -> Package:
    """stages[0] is the base document; the others become variant override layers."""
    opt = opt or Options()
    base = stages[0]
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    stem = slug(base.info.name)
    root = folder / f"{stem}.usda"
    pkg = Package(folder, root, folder / f"{stem}.stj.json")

    base_letter = base.info.letter
    variants_meta = [{"letter": s.info.letter, "override": f"{stem}_{s.info.letter.lower()}.usda"}
                     for s in stages[1:]]
    mesh_files, written = _write_meshes(base, folder, stem, opt)
    # one weather variant per further weather of the base letter: a patch of the sky (fog) and lighting
    wx_meta, wx_patches, wx_ids = [], {}, {}
    first_w = stj_doc.base_weather(base, base_letter)
    for w in base.weathers:
        if first_w is None or w is first_w or w.letter != base_letter:
            continue
        patch = merge_diff(stj_doc.weather_env(first_w), stj_doc.weather_env(w))
        if not patch:
            continue
        vid = f"weather_{slug(w.section, 'w')}"
        wx_ids[w.section] = vid
        wx_patches[vid] = {"environment": patch}
        wx_meta.append({"id": vid, "name": w.section, "override": f"{stem}_wx_{len(wx_meta) + 1}.usda"})
    all_variants = variants_meta + wx_meta
    doc = stj_doc.build_document(base, mesh_files=mesh_files, user_table=user_table, variants=all_variants,
                                 base_letter=base_letter, source_files=(), wx_ids=wx_ids)
    mesh_rels = [str(p.relative_to(folder)).replace("\\", "/") for p in written]
    _root_layer(root, mesh_rels, base.info.name)
    embed(doc, root)
    pkg.files += [root] + written

    for st, vm in zip(stages[1:], variants_meta):
        vstem = f"{stem}_{st.info.letter.lower()}"
        v_files, v_written = _write_meshes(st, folder, vstem, opt, (base, mesh_files))
        vdoc = stj_doc.build_document(st, mesh_files=v_files, user_table=user_table, variants=all_variants,
                                      base_letter=base_letter, wx_ids=wx_ids)
        vdoc.pop("variants", None)
        d = dict(doc)
        d.pop("variants", None)
        for dd in (d, vdoc):                  # diagnostics describe a run, not the stage: never part of a variant patch
            if "x_rbr" in dd:
                dd["x_rbr"] = {k: v for k, v in dd["x_rbr"].items() if k != "diagnostics"}
        patch = merge_diff(d, vdoc)
        patch.pop("_metadata", None)          # provenance stays the base's; the variant only changes the stage
        _variant_layer(folder / vm["override"], root.name, st.info.letter, patch)
        pkg.files += [folder / vm["override"]] + v_written

    for vm in wx_meta:
        _variant_layer(folder / vm["override"], root.name, "", wx_patches[vm["id"]], kind="weather",
                       description=f"RBR weather {vm['name']}: sky and lighting differences from the base weather")
        pkg.files.append(folder / vm["override"])

    pkg.json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pkg.files.append(pkg.json_path)

    tools = find_stj_tools()
    if usdz:
        if tools is None:
            raise Rbr2StjError("--usdz needs the STJ tools (set STJ_TOOLS or clone STJ into reference/STJ)")
        out_z = folder / f"{stem}.usdz"
        r = subprocess.run([sys.executable, str(tools / "stj_usd.py"), "package", str(root), "-o", str(out_z)],
                           capture_output=True, text=True)
        if r.returncode:
            raise Rbr2StjError(f"usdz packaging failed: {(r.stdout + r.stderr).strip()}")
        pkg.usdz = out_z
    if validate:
        pkg.validation = run_validate(root, tools)
    if blend:
        from .blend import write_blend
        pkg.blend = write_blend(root, tools, background=blend_background)
        if pkg.blend[2]:
            pkg.files.append(pkg.blend[2])
    return pkg


def _variant_layer(path: Path, base_name: str, letter: str, patch: dict, kind: str | None = None, description: str | None = None):
    from pxr import Sdf
    if path.exists():
        path.unlink()
    layer = Sdf.Layer.CreateNew(str(path))
    layer.subLayerPaths.append(f"./{base_name}")
    layer.Save()
    kind = kind or stj_doc.VARIANT_KIND.get(letter, "other")
    kind = kind if kind in ("weather", "time_of_day", "season", "other") else "other"
    embed({"_metadata": {"specification": "STJ-OVERRIDE", "version": stj_doc.VERSION, "base": f"./{base_name}",
                         "variant": kind,
                         "description": description or f"RBR variant letter {letter}: only what differs from the base stage."},
           "patch": patch}, path)


def run_validate(root: Path, tools: Path | None, strict: bool = True) -> tuple[int, str]:
    if tools is None:
        return (-1, "STJ validator not found (set STJ_TOOLS or clone STJ into reference/STJ); skipped")
    cmd = [sys.executable, str(tools / "validate.py"), str(root)] + (["--strict"] if strict else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()
