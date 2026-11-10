"""USD layer for the non-terrain `.lbs` visuals (see readers/lbs_extras.py): object lanes, sky dome, clouds, interactive
objects (placed by their stored matrices), reflection and water faces.

Frame: geometry (Y up, left-handed) -> STJ (x, z, y), triangles reversed; matrices become S*M*S (S swaps y and z) so
they keep their handedness. Textures: the first texture of an item is the diffuse; a second one is copied and recorded
on the material (`stj:texture2File`) but not shaded. Baked vertex colour stays the `displayColor` primvar; it is never
applied to the material (no baking). Lane items are alpha tested at 60/255 and drawn two-sided when their kind has bit
0x01 (reRBR notes/formats.md). Everything the format carries beyond that (keys, unknown words, boxes) is kept verbatim
in `stj:extra` JSON on the mesh.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .usd_mesh import _pxr, add_mesh, new_stage, preview_material, textured_material, usd_name

GROUP_ROOT = {"lanes": "lanes", "sky": "sky_dome", "clouds": "clouds", "interactive": "interactive",
              "reflection": "reflection", "water": "water"}


def _swap(m):
    """D3D matrix (row vectors) in the geometry frame -> the STJ frame: S M S with S swapping y and z."""
    perm = (0, 2, 1, 3)
    return [[m[perm[r] * 4 + perm[c]] for c in range(4)] for r in range(4)]


def write_extras(path: Path, items, texture_requests: dict, shift=(0.0, 0.0), exporter=None, visible=None) -> dict:
    """`visible(item) -> bool` (default True) lets the caller switch alternatives off (weather-dependent clouds)."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    from pxr import Gf
    stage = new_stage(path, "extras")
    UsdGeom.Xform.Define(stage, "/extras/materials")
    mats: dict = {}

    def material(item):
        key = (tuple(item.textures), item.group == "lanes")
        if key in mats:
            return mats[key]
        tex = item.textures[0] if item.textures else None
        raw = texture_requests.get(tex, "") if tex is not None else ""
        stem = raw.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        name = usd_name(stem or "texture", "texture")
        p, k = f"/extras/materials/{name}", 2
        while stage.GetPrimAtPath(p):
            p, k = f"/extras/materials/{name}_{k}", k + 1
        exp = exporter.export(raw) if (exporter and raw) else None
        if exp:
            m = textured_material(stage, p, exp[0], exp[1], alpha_threshold=60 / 255 if item.group == "lanes" else 0.5)
        else:
            m = preview_material(stage, p, colour=(0.6, 0.6, 0.6))
        pr = m.GetPrim()
        if raw:
            pr.CreateAttribute("stj:textureRequest", Sdf.ValueTypeNames.String).Set(raw)
        for n, ti in enumerate(item.textures[1:], start=2):
            r2 = texture_requests.get(ti, "")
            e2 = exporter.export(r2) if (exporter and r2) else None
            pr.CreateAttribute(f"stj:texture{n}Request", Sdf.ValueTypeNames.String).Set(r2)
            if e2:
                pr.CreateAttribute(f"stj:texture{n}File", Sdf.ValueTypeNames.String).Set(e2[0])
        mats[key] = m
        return m

    counts: dict[str, int] = {}
    protos: dict = {}
    n_inst = 0
    sway_freqs: list[float] = []
    sway_vertices = 0
    placements: dict[str, list[str]] = {}
    used: set[str] = set()
    for idx, it in enumerate(items):
        n = len(it.positions)
        faces = []
        for a in range(0, len(it.indices) - 2, 3):
            i, j, k = it.indices[a:a + 3]
            if max(i, j, k) < n:
                faces.append((i, k, j))
        if not faces:
            continue
        root = GROUP_ROOT[it.group]
        if f"/extras/{root}" not in used:
            UsdGeom.Xform.Define(stage, f"/extras/{root}")
            used.add(f"/extras/{root}")
        label = usd_name(it.name.rsplit("/", 1)[-1] or f"b{it.block}r{it.run}", "item")
        base = f"/extras/{root}/{label}_{idx}"
        pts = [(p[0] - shift[0], p[2] - shift[1], p[1]) for p in it.positions]
        vis = it.extra.get("visibility") if it.group == "interactive" else None
        if vis:                       # a class prim: referenced by every placement, never drawn itself
            stage.DefinePrim(base).SetSpecifier(Sdf.SpecifierClass)
        mesh = add_mesh(stage, base, pts, faces, material(it))
        pv = UsdGeom.PrimvarsAPI(mesh)
        if any(u is not None for u in it.uvs):
            pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
                Vt.Vec2fArray([(u[0], 1.0 - u[1]) if u else (0.0, 0.0) for u in it.uvs]))
        if any(c is not None for c in it.colours):
            cols = [(((c or 0) >> 16 & 255) / 255.0, ((c or 0) >> 8 & 255) / 255.0, ((c or 0) & 255) / 255.0)
                    for c in it.colours]
            pv.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(cols))
        if it.group == "lanes" and it.kind & 1:
            mesh.CreateDoubleSidedAttr(True)
        if it.sway and any(s[0] > 0 for s in it.sway):
            # RLSingleTextureSway: per vertex {sway weight 0..1, frequency, phase}. Frequency and phase are garbage
            # (uninitialised memory) where the weight is 0, so they are read only where it is positive. Phase is in
            # radians in the file (0..pi) and STJ counts turns.
            tau = 2.0 * math.pi
            w = [min(max(s[0], 0.0), 1.0) if math.isfinite(s[0]) else 0.0 for s in it.sway]
            ph = [(s[2] / tau) % 1.0 if (s[0] > 0 and math.isfinite(s[2]) and abs(s[2]) < 1e4) else 0.0 for s in it.sway]
            pv.CreatePrimvar("stj_wind_weight", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex).Set(Vt.FloatArray(w))
            pv.CreatePrimvar("stj_wind_phase", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex).Set(Vt.FloatArray(ph))
            for s in it.sway:
                if s[0] > 0 and math.isfinite(s[1]) and 0 < s[1] < 100:
                    sway_freqs.append(s[1])
            sway_vertices += sum(1 for x in w if x > 0)
        prim = mesh.GetPrim()
        prim.CreateAttribute("stj:flags", Sdf.ValueTypeNames.Int).Set(int(it.flags))
        prim.CreateAttribute("stj:kind", Sdf.ValueTypeNames.Int).Set(int(it.kind))
        prim.CreateAttribute("stj:stride", Sdf.ValueTypeNames.Int).Set(int(it.stride))
        if it.centre is not None:
            c = it.centre
            prim.CreateAttribute("stj:centre", Sdf.ValueTypeNames.Float3).Set((c[0] - shift[0], c[2] - shift[1], c[1]))
        if any(k != "visibility" for k in it.extra):
            prim.CreateAttribute("stj:extra", Sdf.ValueTypeNames.String).Set(json.dumps({k: v for k, v in it.extra.items() if k != "visibility"}, default=str))
        if visible is not None and not visible(it):
            mesh.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        counts[it.group] = counts.get(it.group, 0) + 1
        # interactive objects: the mesh is a prototype at the origin, placed by every stored matrix
        if vis:
            for vi, v in enumerate(vis):
                m = _swap(v["matrix"])
                m[3][0] -= shift[0]
                m[3][1] -= shift[1]
                xf = UsdGeom.Xform.Define(stage, f"{base}_p{vi}")
                xf.AddTransformOp().Set(Gf.Matrix4d(m))
                xf.GetPrim().GetReferences().AddInternalReference(base)
                xf.GetPrim().CreateAttribute("stj:word", Sdf.ValueTypeNames.Int).Set(int(v["word"]))
                placements.setdefault(it.name, []).append(f"{base}_p{vi}")
                n_inst += 1
    stage.GetRootLayer().Save()
    sway_freqs.sort()
    wind = ({"vertices": sway_vertices, "frequency": sway_freqs[len(sway_freqs) // 2] if sway_freqs else None,
             "frequencies": sorted({round(f, 3) for f in sway_freqs})} if sway_vertices else None)
    return {"items": counts, "interactive_placements": n_inst, "placement_paths": placements, "wind": wind}
