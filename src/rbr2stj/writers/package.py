"""Write an STJ folder package: root `.usda` (STJ document embedded in `customLayerData["stj"]`, spec §7.8), mesh
layers, variant layers and an extracted `<name>.stj.json` for diffing. Optionally a `.usdz` through STJ's own tool.

The STJ tools (`stj_core`, `stj_usd`, `validate`) are used when found (env STJ_TOOLS, `reference/STJ/tools`, or a
sibling `STJ-Standard-Track-Json/tools`); the embedding itself is the 6-line recipe the spec defines, so a package can
be written without them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..convert import Options
from ..errors import Rbr2StjError
from ..ir import Stage
from . import lbs_visual, stj_doc, usd_mesh
from .stj_doc import slug

FACE_LIMIT_USDA = 50_000


def find_stj_tools() -> Path | None:
    cands = []
    if os.environ.get("STJ_TOOLS"):
        cands.append(Path(os.environ["STJ_TOOLS"]))
    here = Path(__file__).resolve()
    for up in (here.parents[3], here.parents[4] if len(here.parents) > 4 else here.parents[3], Path.cwd()):
        cands += [up / "reference" / "STJ" / "tools", up.parent / "STJ-Standard-Track-Json" / "tools"]
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


def _mesh_ext(opt: Options, faces: int) -> str:
    if opt.mesh_format in ("usda", "usdc"):
        return opt.mesh_format
    return "usda" if faces <= FACE_LIMIT_USDA else "usdc"


def _same(a: Stage, b: Stage, *exts: str) -> bool:
    return all(a.file_hashes.get(k) is not None and a.file_hashes.get(k) == b.file_hashes.get(k) for k in exts)


def _write_meshes(stage: Stage, folder: Path, stem: str, opt: Options, base: tuple[Stage, dict] | None = None):
    """Write the stage's mesh layers (reusing the base stage's when the source files are byte-identical, which
    answers PLAN open question 7 per stage); returns (`mesh_files` for build_document, layers written)."""
    mesh_files: dict = {}
    written: list[Path] = []
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
        rel = f"meshes/{stem}_visual.usda"
        info = usd_mesh.write_visual(folder / rel, stage.visual, stage.btb_track, stage.shift)
        stage.diagnostics["visual"] = info
        mesh_files.setdefault("extra", []).append({"id": "visual", "uri": rel, "roles": ["visual"]})
        written.append(folder / rel)
    if stage.lbs_visual:
        shared = None
        if base and _same(stage, base[0], "lbs", "ini"):
            shared = next((e for e in base[1].get("extra", []) if e["id"] == "visual"), None)
        if shared:
            mesh_files.setdefault("extra", []).append(shared)
        else:
            tris = sum(c.triangles for b in stage.lbs_visual for c in b.chunks if c.draw_list != 3)
            rel = f"meshes/{stem}_visual.{_mesh_ext(opt, tris)}"
            info = lbs_visual.write_lbs_visual(folder / rel, stage.lbs_visual, stage.texture_requests, stage.shift)
            stage.diagnostics["visual"] = info
            mesh_files.setdefault("extra", []).append({"id": "visual", "uri": rel, "roles": ["visual"]})
            written.append(folder / rel)
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
                  usdz: bool = False, validate: bool = False) -> Package:
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
    doc = stj_doc.build_document(base, mesh_files=mesh_files, user_table=user_table, variants=variants_meta,
                                 base_letter=base_letter, source_files=())
    mesh_rels = [str(p.relative_to(folder)).replace("\\", "/") for p in written]
    _root_layer(root, mesh_rels, base.info.name)
    embed(doc, root)
    pkg.files += [root] + written

    for st, vm in zip(stages[1:], variants_meta):
        vstem = f"{stem}_{st.info.letter.lower()}"
        v_files, v_written = _write_meshes(st, folder, vstem, opt, (base, mesh_files))
        vdoc = stj_doc.build_document(st, mesh_files=v_files, user_table=user_table, variants=variants_meta,
                                      base_letter=base_letter)
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
    return pkg


def _variant_layer(path: Path, base_name: str, letter: str, patch: dict):
    from pxr import Sdf
    if path.exists():
        path.unlink()
    layer = Sdf.Layer.CreateNew(str(path))
    layer.subLayerPaths.append(f"./{base_name}")
    layer.Save()
    kind = stj_doc.VARIANT_KIND.get(letter, "other")
    kind = kind if kind in ("weather", "time_of_day", "season", "other") else "other"
    embed({"_metadata": {"specification": "STJ-OVERRIDE", "version": stj_doc.VERSION, "base": f"./{base_name}",
                         "variant": kind,
                         "description": f"RBR variant letter {letter}: only what differs from the base stage."},
           "patch": patch}, path)


def run_validate(root: Path, tools: Path | None, strict: bool = True) -> tuple[int, str]:
    if tools is None:
        return (-1, "STJ validator not found (set STJ_TOOLS or clone STJ into reference/STJ); skipped")
    cmd = [sys.executable, str(tools / "validate.py"), str(root)] + (["--strict"] if strict else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()
