"""Classic `.lbs` visual geometry -> a USD layer (P6, stretch goal).

One Mesh per (block, view slot) with a GeomSubset and a material per render chunk. Frame: Y up, left-handed -> STJ
(x, z, y) with the triangles reversed. Chunks of draw list 3 (far LOD) go to their own file, which the STJ document lists
as a `lod` mesh of the near one (they repeat the near geometry at a lower detail). Textures are referenced by name only (`stj:diffuseTexture` = the stem of the stage .ini `Texture<N>`
entry, `stj:textureRequest` = the raw entry); vertex colour (baked light) is kept as `displayColor`.
Layout source: reRBR notes/formats.md "Stage geometry"; see readers/lbs.py.
"""

from __future__ import annotations

from pathlib import Path

from .usd_mesh import _pxr, add_baked_maps, add_mesh, is_cutout, new_stage, preview_material, textured_material, usd_name


def write_lbs_visual(path: Path, blocks, texture_requests: dict, shift=(0.0, 0.0), exporter=None, far: bool = False,
                     shadow_requests: dict | None = None, specular_requests: dict | None = None) -> dict:
    """`far=False`: the near / always chunks; `far=True`: only the far-LOD chunks (draw list 3), for the `lod` mesh."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    stage = new_stage(path, "visual")
    UsdGeom.Xform.Define(stage, "/visual/materials")
    mats: dict = {}
    shadow_requests = shadow_requests or {}
    specular_requests = specular_requests or {}
    used = {"shadow": set(), "specular": set()}

    def material(ch):
        texs = ch.textures
        key = (tuple(texs), ch.shadow, ch.specular)
        tex_index = texs[0] if texs else None
        if key in mats:
            return mats[key]
        raw = texture_requests.get(tex_index, "")
        stem = raw.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        name = usd_name(stem, f"texture{tex_index}") if stem else f"texture{tex_index}"
        p = f"/visual/materials/{name}"
        k = 2
        while stage.GetPrimAtPath(p):
            p, k = f"/visual/materials/{name}_{k}", k + 1
        exp = exporter.export(raw) if (exporter and raw) else None
        m = (textured_material(stage, p, exp[0], exp[1]) if exp else preview_material(stage, p, colour=(0.6, 0.6, 0.6)))
        pr = m.GetPrim()
        pr.CreateAttribute("stj:textureIndex", Sdf.ValueTypeNames.Int).Set(-1 if tex_index is None else int(tex_index))
        if raw:
            pr.CreateAttribute("stj:textureRequest", Sdf.ValueTypeNames.String).Set(raw)
            pr.CreateAttribute("stj:diffuseTexture", Sdf.ValueTypeNames.String).Set(stem)
        for n, ti in enumerate(texs[1:], start=2):      # a second texture: copied and recorded, not shaded
            r2 = texture_requests.get(ti, "")
            e2 = exporter.export(r2) if (exporter and r2) else None
            pr.CreateAttribute(f"stj:texture{n}Request", Sdf.ValueTypeNames.String).Set(r2)
            if e2:
                pr.CreateAttribute(f"stj:texture{n}File", Sdf.ValueTypeNames.String).Set(e2[0])
        sh_rel = sp_rel = None
        if ch.shadow is not None and exporter and shadow_requests.get(ch.shadow):
            e3 = exporter.export(shadow_requests[ch.shadow])
            sh_rel = e3[0] if e3 else None
            pr.CreateAttribute("stj:shadowRequest", Sdf.ValueTypeNames.String).Set(shadow_requests[ch.shadow])
        if ch.specular is not None and exporter and specular_requests.get(ch.specular):
            e4 = exporter.export(specular_requests[ch.specular])
            sp_rel = e4[0] if e4 else None
            pr.CreateAttribute("stj:specularRequest", Sdf.ValueTypeNames.String).Set(specular_requests[ch.specular])
        add_baked_maps(stage, m, sh_rel, sp_rel)
        if sh_rel:
            used["shadow"].add(sh_rel)
        if sp_rel:
            used["specular"].add(sp_rel)
        mats[key] = m
        return m

    meshes = tris_total = 0
    for bi, block in enumerate(blocks):
        UsdGeom.Xform.Define(stage, f"/visual/block{bi}")
        by_slot: dict[tuple[int, bool], list] = {}
        for ch in block.chunks:
            if (ch.draw_list == 3) == far:
                by_slot.setdefault((ch.slot, far), []).append(ch)
        for (slot, _f), chunks in by_slot.items():
            view = block.views[slot]
            n = len(view.positions)
            faces, subsets = [], []
            for ch in chunks:
                start = len(faces)
                for t in range(ch.triangles):
                    a = ch.start_index + 3 * t
                    if a + 2 >= len(view.indices):
                        break
                    i, j, k = view.indices[a:a + 3]
                    if max(i, j, k) >= n:
                        continue                    # some community stages index past the buffer: skipped
                    faces.append((i, k, j))         # reversed winding
                if len(faces) > start:
                    subsets.append((ch, list(range(start, len(faces)))))
            if not faces:
                continue
            pts = [(p[0] - shift[0], p[2] - shift[1], p[1]) for p in view.positions]
            mesh = add_mesh(stage, f"/visual/block{bi}/block{bi}_view{slot}", pts, faces)
            pv = UsdGeom.PrimvarsAPI(mesh)
            if view.uvs and view.uvs[0] is not None:
                pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
                    Vt.Vec2fArray([(u, 1.0 - v) for u, v in view.uvs]))
            for label, name in (("uv1", "st1"), ("spec", "st_specular"), ("shadow", "st_shadow")):
                if label in view.extra_uvs:
                    pv.CreatePrimvar(name, Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
                        Vt.Vec2fArray([(u, 1.0 - v) for u, v in view.extra_uvs[label]]))
            if view.normals:
                mesh.CreateNormalsAttr(Vt.Vec3fArray([(n[0], n[2], n[1]) for n in view.normals]))
                mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
            cols = [(((c or 0) >> 16 & 255) / 255.0, ((c or 0) >> 8 & 255) / 255.0, ((c or 0) & 255) / 255.0)
                    for c in view.colours]
            pv.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(cols))
            for ci, (ch, idx) in enumerate(subsets):
                sub = UsdGeom.Subset.CreateGeomSubset(mesh, f"chunk{ci}", UsdGeom.Tokens.face, Vt.IntArray(idx),
                                                      UsdShade.Tokens.materialBind)
                UsdShade.MaterialBindingAPI.Apply(sub.GetPrim()).Bind(material(ch))
                if is_cutout(material(ch)):
                    mesh.CreateDoubleSidedAttr(True)       # spec 7.7: a card is two-sided
                sub.GetPrim().CreateAttribute("stj:drawList", Sdf.ValueTypeNames.Int).Set(int(ch.draw_list))
            meshes += 1
            tris_total += len(faces)
    stage.GetRootLayer().Save()
    return {"meshes": meshes, "triangles": tris_total, "shadow_maps": sorted(used["shadow"]),
            "specular_maps": sorted(used["specular"])}
