"""USD meshes for the STJ package: collision mesh with `stj_surf_*` primvars, scenery prototypes, BTB visual meshes.

Conventions (STJ spec §7.7): `upAxis = "Z"`, `metersPerUnit = 1`; per-face layer indices are **uniform** int primvars
(three of them, `<prefix>_0..2`), per-corner weights a **faceVarying** float2 primvar (`wa, wb`; `wc = 1 - wa - wb`),
so no vertex is split where the layer set changes. STJ primvars use an `stj_` prefix, not the `stj:` namespace.
"""

from __future__ import annotations

from pathlib import Path

from ..ir import CollisionMesh, Prop


def _pxr():
    from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt
    return Sdf, Usd, UsdGeom, UsdShade, Vt


def new_stage(path: Path, root: str):
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    top = UsdGeom.Xform.Define(stage, f"/{root}")
    stage.SetDefaultPrim(top.GetPrim())
    return stage


def preview_material(stage, path: str, colour=(0.55, 0.5, 0.42), roughness=0.9):
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/surface")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(tuple(colour))
    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def textured_material(stage, path: str, tex_rel: str, alpha: bool = False, roughness=0.9, alpha_threshold=0.5):
    """UsdPreviewSurface whose diffuse (and, with `alpha`, cut-out opacity) comes from `tex_rel`, an asset path relative
    to the mesh layer (`../textures/x.png`), sampled with the mesh's `st` primvar."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/surface")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    st = UsdShade.Shader.Define(stage, f"{path}/st")
    st.CreateIdAttr("UsdPrimvarReader_float2")
    st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    tx = UsdShade.Shader.Define(stage, f"{path}/diffuse")
    tx.CreateIdAttr("UsdUVTexture")
    tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("../" + tex_rel))
    tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
    tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
    tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
    tx.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    tx.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
    if alpha:
        tx.CreateOutput("a", Sdf.ValueTypeNames.Float)
        sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(tx.ConnectableAPI(), "a")
        sh.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(float(alpha_threshold))
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def add_baked_maps(stage, material, shadow_rel: str | None = None, specular_rel: str | None = None):
    """The game's own shadow map and specular map of a material, read through the mesh's `st_shadow` / `st_specular`
    primvars. The shadow map (its red channel multiplies the lit colour in the game) goes to the `occlusion` input, the
    specular map to `specularColor` with the specular workflow; the files are also named in `stj:` attributes."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    path = str(material.GetPath())
    sh = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/surface"))
    for rel, var, name, out, space in ((shadow_rel, "st_shadow", "shadow", "r", "raw"),
                                        (specular_rel, "st_specular", "specular", "rgb", "sRGB")):
        if not rel:
            continue
        st = UsdShade.Shader.Define(stage, f"{path}/st_{name}")
        st.CreateIdAttr("UsdPrimvarReader_float2")
        st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(var)
        tx = UsdShade.Shader.Define(stage, f"{path}/{name}")
        tx.CreateIdAttr("UsdUVTexture")
        tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("../" + rel))
        tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
        tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        tx.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(space)
        if out == "r":
            tx.CreateOutput("r", Sdf.ValueTypeNames.Float)
            sh.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(tx.ConnectableAPI(), "r")
        else:
            tx.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            sh.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
            sh.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
        material.GetPrim().CreateAttribute(f"stj:{name}Map", Sdf.ValueTypeNames.String).Set(rel)


def btb_uv_slots(effect: str | None) -> dict:
    """Which UV set (index into the diffuse-first list; `st<n>` on the mesh) each input of a BTB effect reads: after the
    diffuse set come bump, specular, multiplier, additive, lerp, for the inputs the effect has (reRBR `Effect::uv_slots`)."""
    name = (effect or "").lower()
    want = [("bump", "bump" in name), ("specular", "specular" in name), ("multiplier", "multiplyadd" in name),
            ("additive", "multiplyadd" in name), ("lerp", "vertlerp" in name)]
    out, nxt = {}, 1
    for key, on in want:
        if on:
            out[key] = nxt
            nxt += 1
    return out


def _wire_btb_maps(stage, material, sec, files: dict):
    """Bump (normalTexture) -> `normal`; specular (specularTexture) -> `specularColor` with the specular workflow and a
    roughness from SpecularPower (Blinn-Phong exponent P -> sqrt(2 / (P + 2))). Each reads its own primvar."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    slots = btb_uv_slots(sec.effect)
    path = str(material.GetPath())
    sh = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/surface"))
    for key, slot, name, space in (("normalTexture", "bump", "normalmap", "raw"), ("specularTexture", "specular", "specmap", "sRGB")):
        rel = files.get(key)
        if not rel or slot not in slots:
            continue
        st = UsdShade.Shader.Define(stage, f"{path}/st_{name}")
        st.CreateIdAttr("UsdPrimvarReader_float2")
        st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(f"st{slots[slot]}")
        tx = UsdShade.Shader.Define(stage, f"{path}/{name}")
        tx.CreateIdAttr("UsdUVTexture")
        tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("../" + rel))
        tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
        tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        tx.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(space)
        tx.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        if key == "normalTexture":
            tx.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set((2.0, 2.0, 2.0, 1.0))
            tx.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set((-1.0, -1.0, -1.0, 0.0))
            sh.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
        else:
            sh.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
            sh.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
            power = float(sec.extras.get("SpecularPower", 4.0))
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(round((2.0 / (power + 2.0)) ** 0.5, 4))
            material.GetPrim().CreateAttribute("stj:roughnessFromSpecularPower", Sdf.ValueTypeNames.Float).Set(power)


def _wire_btb_mtlx(stage, material, sec, files: dict, diffuse_rel: str | None):
    """MaterialX network (`outputs:mtlx:surface`, an OpenPBR surface) next to the UsdPreviewSurface fallback for what that
    cannot express: the second diffuse of VertLerp (mix by the `stj_blend` primvar) and MultiplyAdd (diffuse * multiplier
    + additive). Each map reads its own UV set (`st<slot>`). The combination is the reading of the effect names, not a
    port of the shader, and is flagged as such in `stj:mtlxNote`."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    name = (sec.effect or "").lower()
    lerp, madd = "vertlerp" in name, "multiplyadd" in name
    slots = btb_uv_slots(sec.effect)
    if not diffuse_rel or not ((lerp and files.get("secondDiffuseTexture")) or (madd and files.get("multiplierTexture"))):
        return
    path = f"{material.GetPath()}/mtlx"
    UsdGeom.Scope.Define(stage, path)
    count = [0]

    def node(nid, outtype, **ins):
        count[0] += 1
        sh = UsdShade.Shader.Define(stage, f"{path}/{nid[3:]}_{count[0]}")
        sh.CreateIdAttr(nid)
        for key, (typ, val) in ins.items():
            if isinstance(val, tuple) and val and isinstance(val[0], UsdShade.Shader):
                sh.CreateInput(key, typ).ConnectToSource(val[0].ConnectableAPI(), val[1])
            else:
                sh.CreateInput(key, typ).Set(val)
        sh.CreateOutput("out", outtype)
        return (sh, "out")

    def image(rel, var):
        uv = node("ND_geompropvalue_vector2", Sdf.ValueTypeNames.Float2, geomprop=(Sdf.ValueTypeNames.String, var))
        img = node("ND_image_color3", Sdf.ValueTypeNames.Color3f, file=(Sdf.ValueTypeNames.Asset, Sdf.AssetPath("../" + rel)),
                   texcoord=(Sdf.ValueTypeNames.Float2, uv), uaddressmode=(Sdf.ValueTypeNames.String, "periodic"),
                   vaddressmode=(Sdf.ValueTypeNames.String, "periodic"))
        img[0].GetInput("file").GetAttr().SetColorSpace("srgb_texture")
        return img

    C3, F = Sdf.ValueTypeNames.Color3f, Sdf.ValueTypeNames.Float
    colour = image(diffuse_rel, "st")
    if lerp and files.get("secondDiffuseTexture"):
        second = image(files["secondDiffuseTexture"], f"st{slots['lerp']}")
        weight = node("ND_geompropvalue_float", F, geomprop=(Sdf.ValueTypeNames.String, "stj_blend"))
        colour = node("ND_mix_color3", C3, bg=(C3, colour), fg=(C3, second), mix=(F, weight))
    if madd and files.get("multiplierTexture"):
        colour = node("ND_multiply_color3", C3, in1=(C3, colour), in2=(C3, image(files["multiplierTexture"], f"st{slots['multiplier']}")))
        if files.get("additiveTexture"):
            colour = node("ND_add_color3", C3, in1=(C3, colour), in2=(C3, image(files["additiveTexture"], f"st{slots['additive']}")))
    surf = node("ND_open_pbr_surface_surfaceshader", Sdf.ValueTypeNames.Token, base_color=(C3, colour), specular_roughness=(F, 0.9))
    UsdShade.Material(material.GetPrim()).CreateSurfaceOutput("mtlx").ConnectToSource(surf[0].ConnectableAPI(), "out")
    import json
    spec = {"diffuse": diffuse_rel}
    if lerp and files.get("secondDiffuseTexture"):
        spec.update(second=files["secondDiffuseTexture"], second_uv=f"st{slots['lerp']}")
    if madd and files.get("multiplierTexture"):
        spec.update(multiplier=files["multiplierTexture"], multiplier_uv=f"st{slots['multiplier']}")
        if files.get("additiveTexture"):
            spec.update(additive=files["additiveTexture"], additive_uv=f"st{slots['additive']}")
    material.GetPrim().CreateAttribute("stj:mtlxSpec", Sdf.ValueTypeNames.String).Set(json.dumps(spec))
    material.GetPrim().CreateAttribute("stj:mtlxNote", Sdf.ValueTypeNames.String).Set(
        "derived view: " + ("mix(diffuse, secondDiffuse, stj_blend)" if lerp else "diffuse") +
        (" * multiplier + additive" if madd else "") + "; the combination is inferred from the effect name")


def is_cutout(material) -> bool:
    """A material with an alpha-test threshold (spec 7.7: a mesh bound to it is a card and must be doubleSided)."""
    if material is None:
        return False
    sh = material.GetPrim().GetChild("surface")
    if not sh:
        return False
    a = sh.GetAttribute("inputs:opacityThreshold")
    return bool(a and a.Get())


def add_mesh(stage, path: str, points, faces, material=None, normals_from_winding: bool = True):
    """A triangle Mesh at `path`. Returns the UsdGeom.Mesh."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray([tuple(round(c, 4) for c in p) for p in points]))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(faces)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    if material is not None:
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        if is_cutout(material):
            mesh.CreateDoubleSidedAttr(True)
    return mesh


def set_surface_primvars(mesh, prefix_layers: str, prefix_weights: str, state):
    """Write one surface state's three uniform layer primvars and the faceVarying weights."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    pv = UsdGeom.PrimvarsAPI(mesh)
    for q in range(3):
        pv.CreatePrimvar(f"{prefix_layers}_{q}", Sdf.ValueTypeNames.IntArray, UsdGeom.Tokens.uniform).Set(
            Vt.IntArray([lay[q] for lay in state.layers]))
    pv.CreatePrimvar(prefix_weights, Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(
        Vt.Vec2fArray([tuple(w) for w in state.weights]))


def state_names(state, multi: bool):
    """(layers prefix, weights primvar) for a state: plain names for a single state, suffixed when several."""
    if not multi:
        return "stj_surf_layers", "stj_surf_weights"
    return f"stj_surf_layers_{state.key}", f"stj_surf_weights_{state.key}"


def write_collision(path: Path, mesh: CollisionMesh, material_name: str = "Ground", multi: bool | None = None):
    """Write the collision mesh layer. Returns {state key: (layers prefix, weights primvar)}."""
    stage = new_stage(path, "collision")
    mat = preview_material(stage, f"/collision/materials/{material_name}")
    m = add_mesh(stage, "/collision/mesh", mesh.points, mesh.faces, mat)
    multi = len(mesh.states) > 1 if multi is None else multi
    names = {}
    for st in mesh.states:
        lp, wp = state_names(st, multi)
        set_surface_primvars(m, lp, wp, st)
        names[st.key] = (lp, wp)
    stage.GetRootLayer().Save()
    return names


def write_props(path: Path, props_by_id: dict[str, Prop]):
    """One Xform with a Mesh per prototype at /props/<id>/<id> (the mesh carries the prototype's name); returns {id: prim path of the Xform}."""
    stage = new_stage(path, "props")
    out = {}
    for pid, p in props_by_id.items():
        base = f"/props/{pid}"
        from pxr import UsdGeom
        UsdGeom.Xform.Define(stage, base)
        add_mesh(stage, f"{base}/{pid}", p.vertices, p.faces)
        out[pid] = base
    stage.GetRootLayer().Save()
    return out


# ── BTB visual meshes (P5) ───────────────────────────────────────────────────

def usd_name(text: str, fallback: str = "x") -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9_]", "_", text or "")
    s = re.sub(r"_+", "_", s).strip("_") or fallback
    return s if not s[0].isdigit() else "_" + s


def _quat_from_columns(ex, ey, ez):
    """Unit quaternion (w, x, y, z) of the rotation whose columns are the images of the local x, y, z axes."""
    import math
    m00, m10, m20 = ex
    m01, m11, m21 = ey
    m02, m12, m22 = ez
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w, x, y, z = (m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w, x, y, z = (m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w, x, y, z = (m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    return (w / n, x / n, y / n, z / n)


def _finite(pos, q, sc):
    """A degenerate matrix can decompose to NaN, which USD text cannot read back: use identity pose / unit scale instead."""
    import math
    ok = all(math.isfinite(c) for c in (*pos, *q, *sc))
    return (pos, q, sc, True) if ok else ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0), False)



def decompose_d3d(m, shift=(0.0, 0.0)):
    """A D3D row-major 4x4 -> (position, quaternion wxyz, scale) in the STJ frame, plus a flag for shear/mirror.

    Row i of the matrix is the image of local D3D axis i; STJ axes are (x, z_d3d, y_d3d), so the STJ local axes
    x, y, z map to D3D rows 0, 2, 1 and each image vector (a, b, c) becomes (a, c, b)."""
    import math
    rows = [m[0:3], m[4:7], m[8:11]]
    norms = [math.sqrt(sum(c * c for c in r)) or 1.0 for r in rows]
    rn = [tuple(c / n for c in r) for r, n in zip(rows, norms)]
    swap = lambda v: (v[0], v[2], v[1])  # noqa: E731
    ex, ey, ez = swap(rn[0]), swap(rn[2]), swap(rn[1])
    scale = (norms[0], norms[2], norms[1])
    # det of the column matrix: negative = mirrored; orthogonality: shear
    cross = (ey[1] * ez[2] - ey[2] * ez[1], ey[2] * ez[0] - ey[0] * ez[2], ey[0] * ez[1] - ey[1] * ez[0])
    det = ex[0] * cross[0] + ex[1] * cross[1] + ex[2] * cross[2]
    ortho = max(abs(sum(a * b for a, b in zip(u, v))) for u, v in ((ex, ey), (ex, ez), (ey, ez)))
    flag = det < 0 or ortho > 0.02
    if det < 0:
        ex = tuple(-c for c in ex)
        scale = (-scale[0], scale[1], scale[2])
    pos = (m[12] - shift[0], m[14] - shift[1], m[13])
    return pos, _quat_from_columns(ex, ey, ez), scale, flag


def write_visual(path: Path, visuals, track, shift=(0.0, 0.0), exporter=None):
    """BTB visual meshes: one prototype Mesh per `.x`, placed by a PointInstancer (not expanded) per object, or by a
    transform when an object has a single placement. Materials are named after the materials.ini sections and carry
    the diffuse texture NAME only (`stj:diffuseTexture`): textures are referenced, never embedded."""
    Sdf, Usd, UsdGeom, UsdShade, Vt = _pxr()
    from pxr import Gf
    stage = new_stage(path, "visual")
    UsdGeom.Xform.Define(stage, "/visual/materials")
    mats: dict[str, object] = {}
    flagged = 0

    def material(name: str):
        key = name.lower()
        if key in mats:
            return mats[key]
        sec = track.materials.get(key)
        exp = exporter.export(sec.diffuse) if (exporter and sec is not None and sec.diffuse) else None
        mpath = f"/visual/materials/{usd_name(name, 'material')}"
        m = (textured_material(stage, mpath, exp[0], exp[1]) if exp
             else preview_material(stage, mpath, colour=(0.6, 0.6, 0.6)))
        prim = m.GetPrim()
        prim.CreateAttribute("stj:sourceName", Sdf.ValueTypeNames.String).Set(name)
        if sec is not None:
            if sec.diffuse:
                tex = sec.diffuse.replace("\\", "/").rsplit("/", 1)[-1]
                prim.CreateAttribute("stj:diffuseTexture", Sdf.ValueTypeNames.String).Set(tex)
            if sec.effect:
                prim.CreateAttribute("stj:btbEffect", Sdf.ValueTypeNames.String).Set(sec.effect)
            if sec.technique:
                prim.CreateAttribute("stj:btbTechnique", Sdf.ValueTypeNames.String).Set(sec.technique)
            # the other maps of the effect: copied and recorded; bump and specular are also wired (each reads its own
            # UV set, the primvar st<slot> written on the meshes); second diffuse, multiplier and additive have no
            # UsdPreviewSurface input and stay recorded
            files = {}
            for key, val in sec.extras.items():
                if key == "SpecularPower":
                    prim.CreateAttribute("stj:specularPower", Sdf.ValueTypeNames.Float).Set(float(val))
                elif key == "SpecularColor":
                    prim.CreateAttribute("stj:specularColor", Sdf.ValueTypeNames.Color3f).Set(tuple(float(c) for c in val))
                else:
                    ex = exporter.export(val) if exporter else None
                    attr = "stj:" + key[0].lower() + key[1:]
                    prim.CreateAttribute(attr, Sdf.ValueTypeNames.String).Set(val.replace("\\", "/").rsplit("/", 1)[-1])
                    if ex:
                        prim.CreateAttribute(attr + "File", Sdf.ValueTypeNames.String).Set(ex[0])
                        files[key] = ex[0]
            _wire_btb_maps(stage, m, sec, files)
            if sec.diffuse and exp:
                _wire_btb_mtlx(stage, m, sec, files, exp[0])
        mats[key] = m
        return m

    count = instances = 0
    objects: dict = {}                    # object index -> where its prototype and instancer are (for scenery references)
    for item in visuals:
        o, mesh, matrices = item["object"], item["mesh"], item["matrices"]
        base = f"/visual/obj_{o.index}_{usd_name(o.file, 'mesh')}"
        UsdGeom.Xform.Define(stage, base)
        many = len(matrices) > 1
        if many:
            inst = UsdGeom.PointInstancer.Define(stage, f"{base}/instances")
            proto_path = f"{base}/instances/proto"             # prototypes live under their instancer
            holder = inst.GetPrim()
        else:
            proto_path = f"{base}/placed"
            holder = stage.GetPrimAtPath(base)
        UsdGeom.Xform.Define(stage, proto_path)
        verts = [(v[0], v[2], v[1]) for v in mesh.vertices]
        tris = [(a, c, b) for a, b, c in mesh.triangles]                 # reversed winding (handedness flip)
        names = mesh.material_names
        single_material = len(set(mesh.face_materials)) <= 1
        leaf = usd_name(o.file, "mesh")                                 # the .x name: a DCC shows it instead of "mesh.017"
        usd = add_mesh(stage, f"{proto_path}/{leaf}", verts, tris, material(names[0]) if names and single_material else None)
        if mesh.uvs is not None:
            UsdGeom.PrimvarsAPI(usd).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                   UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray([(u, 1.0 - v) for u, v in mesh.uvs]))
        for k, uvset in enumerate(mesh.extra_uvs, start=1):       # st1, st2 ...: the UV sets after the diffuse one
            UsdGeom.PrimvarsAPI(usd).CreatePrimvar(f"st{k}", Sdf.ValueTypeNames.TexCoord2fArray,
                                                   UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray([(u, 1.0 - v) for u, v in uvset]))
        if mesh.blend:
            UsdGeom.PrimvarsAPI(usd).CreatePrimvar("stj_blend", Sdf.ValueTypeNames.FloatArray,
                                                   UsdGeom.Tokens.vertex).Set(Vt.FloatArray(mesh.blend))
        if names and not single_material:
            for mi, nm in enumerate(names):
                idx = [i for i, fm in enumerate(mesh.face_materials) if fm == mi]
                if idx:
                    sub = UsdGeom.Subset.CreateGeomSubset(usd, f"m{mi}", UsdGeom.Tokens.face, Vt.IntArray(idx),
                                                          UsdShade.Tokens.materialBind)
                    UsdShade.MaterialBindingAPI.Apply(sub.GetPrim()).Bind(material(nm))
                    if is_cutout(material(nm)):
                        usd.CreateDoubleSidedAttr(True)
        count += 1
        if not many:
            pos, q, sc, flag = decompose_d3d(matrices[0], shift)
            pos, q, sc, fin = _finite(pos, q, sc)
            flag += not fin
            flagged += flag
            xf = UsdGeom.Xform(stage.GetPrimAtPath(proto_path))
            xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
            xf.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(*q))
            xf.AddScaleOp().Set(Gf.Vec3f(*sc))
            instances += 1
        else:
            inst.CreatePrototypesRel().AddTarget(proto_path)
            P, Q, S = [], [], []
            for m in matrices:
                pos, q, sc, flag = decompose_d3d(m, shift)
                pos, q, sc, fin = _finite(pos, q, sc)
                flag += not fin
                flagged += flag
                P.append(Gf.Vec3f(*pos))
                Q.append(Gf.Quath(*q))
                S.append(Gf.Vec3f(*sc))
            inst.CreatePositionsAttr(Vt.Vec3fArray(P))
            inst.CreateOrientationsAttr(Vt.QuathArray(Q))
            inst.CreateScalesAttr(Vt.Vec3fArray(S))
            inst.CreateProtoIndicesAttr(Vt.IntArray([0] * len(matrices)))
            instances += len(matrices)
            objects[o.index] = {"proto": f"{proto_path}/{leaf}", "instancer": f"{base}/instances", "count": len(matrices)}
        holder.CreateAttribute("stj:btbObject", Sdf.ValueTypeNames.Int).Set(o.index)
        if o.lod_in is not None:
            holder.CreateAttribute("stj:lodIn", Sdf.ValueTypeNames.Float).Set(float(o.lod_in))
        if o.lod_out is not None:
            holder.CreateAttribute("stj:lodOut", Sdf.ValueTypeNames.Float).Set(float(o.lod_out))
    stage.GetRootLayer().Save()
    return {"prototypes": count, "instances": instances, "flagged_matrices": flagged, "objects": objects}
