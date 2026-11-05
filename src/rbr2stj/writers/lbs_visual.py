"""Classic `.lbs` visual geometry -> a USD layer (P6, stretch goal).

One Mesh per (block, view slot) with a GeomSubset and a material per render chunk. Frame: Y up, left-handed -> STJ
(x, z, y) with the triangles reversed. Chunks of draw list 3 (far LOD) are dropped: they repeat the near geometry at a
lower detail. Textures are referenced by name only (`stj:diffuseTexture` = the stem of the stage .ini `Texture<N>`
entry, `stj:textureRequest` = the raw entry); vertex colour (baked light) is kept as `displayColor`.
Layout source: reRBR notes/formats.md "Stage geometry"; see readers/lbs.py.
"""

from __future__ import annotations

from pathlib import Path

from .usd_mesh import _pxr, add_mesh, new_stage, preview_material, usd_name


def write_lbs_visual(path: Path, blocks, texture_requests: dict, shift=(0.0, 0.0)) -> dict:
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    stage = new_stage(path, "visual")
    UsdGeom.Xform.Define(stage, "/visual/materials")
    mats: dict = {}

    def material(tex_index):
        if tex_index in mats:
            return mats[tex_index]
        raw = texture_requests.get(tex_index, "")
        stem = raw.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        name = usd_name(stem, f"texture{tex_index}") if stem else f"texture{tex_index}"
        p = f"/visual/materials/{name}"
        k = 2
        while stage.GetPrimAtPath(p):
            p, k = f"/visual/materials/{name}_{k}", k + 1
        m = preview_material(stage, p, colour=(0.6, 0.6, 0.6))
        pr = m.GetPrim()
        pr.CreateAttribute("stj:textureIndex", Sdf.ValueTypeNames.Int).Set(-1 if tex_index is None else int(tex_index))
        if raw:
            pr.CreateAttribute("stj:textureRequest", Sdf.ValueTypeNames.String).Set(raw)
            pr.CreateAttribute("stj:diffuseTexture", Sdf.ValueTypeNames.String).Set(stem)
        mats[tex_index] = m
        return m

    meshes = tris_total = 0
    for bi, block in enumerate(blocks):
        UsdGeom.Xform.Define(stage, f"/visual/block{bi}")
        by_slot: dict[int, list] = {}
        for ch in block.chunks:
            if ch.draw_list != 3:
                by_slot.setdefault(ch.slot, []).append(ch)
        for slot, chunks in by_slot.items():
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
            mesh = add_mesh(stage, f"/visual/block{bi}/view{slot}", pts, faces)
            pv = UsdGeom.PrimvarsAPI(mesh)
            if view.uvs and view.uvs[0] is not None:
                pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
                    Vt.Vec2fArray([(u, 1.0 - v) for u, v in view.uvs]))
            cols = [(((c or 0) >> 16 & 255) / 255.0, ((c or 0) >> 8 & 255) / 255.0, ((c or 0) & 255) / 255.0)
                    for c in view.colours]
            pv.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(cols))
            for ci, (ch, idx) in enumerate(subsets):
                sub = UsdGeom.Subset.CreateGeomSubset(mesh, f"chunk{ci}", UsdGeom.Tokens.face, Vt.IntArray(idx),
                                                      UsdShade.Tokens.materialBind)
                tex = ch.textures[0] if ch.textures else None
                UsdShade.MaterialBindingAPI.Apply(sub.GetPrim()).Bind(material(tex))
                sub.GetPrim().CreateAttribute("stj:drawList", Sdf.ValueTypeNames.Int).Set(int(ch.draw_list))
            meshes += 1
            tris_total += len(faces)
    stage.GetRootLayer().Save()
    return {"meshes": meshes, "triangles": tris_total}
