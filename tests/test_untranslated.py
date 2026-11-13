"""Data that used to be dropped and is now carried: shadow maps, wet grip, scripted objects, BTB material maps."""
import math
import struct
import zipfile
from pathlib import Path

from rbr2stj.convert.classic import _scripted_marshals
from rbr2stj.readers.btb import _decimal_triplet
from rbr2stj.readers.lbs import AnimationObject
from rbr2stj.textures import TextureExporter, TextureSource
from rbr2stj.writers.stj_doc import tyre_mu


def test_shadow_dat_becomes_a_png_with_the_same_texels(tmp_path):
    from PIL import Image
    w, h = 4, 2
    texels = bytes([0, 17, 34, 51, 68, 85, 102, 255])
    z = tmp_path / "track-9_E_textures.rbz"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("track-9_E_textures/shadow_0.dat", struct.pack("<II", w, h) + texels)
    exp = TextureExporter(TextureSource([z], [], ["track-9_e_textures"]), tmp_path / "out" / "textures")
    res = exp.export("shadow_0.dat")
    assert res and res[0].endswith(".png")
    im = Image.open(tmp_path / "out" / res[0])
    assert im.size == (w, h) and im.mode == "L" and im.tobytes() == texels


def test_bad_shadow_header_is_reported_not_raised(tmp_path):
    z = tmp_path / "t_textures.rbz"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("t_textures/shadow_1.dat", struct.pack("<II", 4096, 4096) + b"\0" * 8)
    exp = TextureExporter(TextureSource([z], [], []), tmp_path / "o")
    assert exp.export("shadow_1.dat") is None and "shadow_1.dat" in exp.failed


def test_wet_grip_follows_the_games_formula():
    tyre = {"SFric": [0.8, 0.8, 1.0], "WetSlope": [-0.3, -0.2, -0.3], "WetnessDesign": 0.5}
    assert tyre_mu(tyre, 2, 0.5) == 1.0                       # at the design wetness the slope term vanishes
    assert math.isclose(tyre_mu(tyre, 2, 1.0), 1.0 - 0.3 * 0.5)
    assert math.isclose(tyre_mu(tyre, 2, 0.0), 1.0 - 0.3 * 0.5)
    assert tyre_mu({"SFric": [0.7]}, 0, 1.0) == 0.7            # no WetnessDesign: the dry base only
    assert tyre_mu(tyre, 9, 0.0) is None


def _obj(i, script, pos=(10.0, 2.0, 30.0), yaw=90.0, name="Untitled"):
    return AnimationObject(name, (i, 0, 0), (script, "RUN.LUA"), [pos, (0.0, yaw, 0.0), (1, 1, 1), pos, pos], 0)


def test_scripted_objects_become_posts_and_vehicles():
    x = {}
    out = _scripted_marshals([
        _obj(1, "MAPS/GENERIC OBJECTS/SCRIPTS/LOAD_SPECTATOR_STANDARD_02.LUA"),
        _obj(2, "MAPS/GENERIC OBJECTS/SCRIPTS/LOAD_MARSHAL_STAND.LUA"),
        _obj(3, "MAPS/GENERIC OBJECTS/SCRIPTS/LOAD_MEDIA_STAND.LUA"),
        _obj(4, "MAPS/GENERIC OBJECTS/SCRIPTS/LOAD_RESCUECHOPPER.LUA"),
        _obj(5, "MAPS/GENERIC OBJECTS/SCRIPTS/LOAD_BIRD.LUA"),
    ], lambda p: (p[0] - 1.0, p[1] - 1.0, p[2]), x)
    kinds = [p["kind"] for p in out["posts"]]
    assert kinds == ["spectator_point", "flag_post", "other"]
    assert out["posts"][0]["position"] == [9.0, 29.0, 2.0]          # geometry (x, y up, z) -> STJ (x, y, z up), shifted
    assert out["vehicles"][0]["kind"] == "helicopter" and out["vehicles"][0]["pose"]["heading"] == 0.0
    assert x["scripted_objects"]["not_placed:load_bird.lua"] == 1


def test_btb_decimal_comma_colour():
    assert _decimal_triplet("0,8627451,0,8627451,0,8627451") == (0.8627451,) * 3
    assert _decimal_triplet("0.5, 0.25, 1") == (0.5, 0.25, 1.0)
    assert _decimal_triplet("nonsense") is None


def test_btb_uv_slots_follow_the_effect():
    from rbr2stj.writers.usd_mesh import btb_uv_slots
    assert btb_uv_slots("BTB_Diffuse") == {}
    assert btb_uv_slots("BTB_DiffuseBumpSpecular") == {"bump": 1, "specular": 2}
    assert btb_uv_slots("BTB_DiffuseBumpSpecularVertLerp") == {"bump": 1, "specular": 2, "lerp": 3}
    assert btb_uv_slots("BTB_DiffuseMultiplyAddVertLerp") == {"multiplier": 1, "additive": 2, "lerp": 3}


def _bits(*floats):
    return [struct.unpack("<I", struct.pack("<f", f))[0] for f in floats]


def test_x_extra_streams_decl_and_fvf():
    from rbr2stj.readers.xfile import _Obj, _extra_streams
    nv = 2
    # DeclData: blend weight (FLOAT1, usage 1), texcoord index 1 (FLOAT2, usage 5), tangent (FLOAT3, usage 6)
    els = [0, 0, 1, 0, 1, 0, 5, 1, 2, 0, 6, 0]
    data = _bits(0.25, 0.1, 0.2, 1, 0, 0) + _bits(0.75, 0.3, 0.4, 0, 1, 0)
    decl = _Obj("DeclData", ints=[3, *els, len(data), *data])
    # FVFData: dwFVF 0x202 = two further UV sets
    fdata = _bits(0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
    fvf = _Obj("FVFData", ints=[0x202, len(fdata), *fdata])
    sets, blend = _extra_streams(_Obj("Mesh", children=[decl, fvf]), nv)
    assert blend == [0.25, 0.75]
    assert len(sets) == 3 and [round(c, 2) for c in sets[0][1]] == [0.3, 0.4]      # usage index 1 first (stable order)
    assert [round(c, 2) for c in sets[1][0]] == [0.5, 0.6]       # FVF set 0 (usage index 1, after the DeclData one)
    assert [round(c, 2) for c in sets[2][0]] == [0.7, 0.8]       # FVF set 1


def test_btb_vertlerp_writes_a_materialx_mix(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("pxr")
    from types import SimpleNamespace
    from pxr import Usd, UsdShade
    from rbr2stj.writers import usd_mesh
    stage = usd_mesh.new_stage(tmp_path / "m.usda", "m")
    mat = usd_mesh.textured_material(stage, "/m/mat", "textures/a.png")
    sec = SimpleNamespace(effect="BTB_DiffuseVertLerp")
    usd_mesh._wire_btb_mtlx(stage, mat, sec, {"secondDiffuseTexture": "textures/b.png"}, "textures/a.png")
    ids = {s.GetPrim().GetName().split("_")[0] for s in (UsdShade.Shader(p) for p in stage.Traverse() if p.GetName() != "mtlx" and "mtlx" in str(p.GetPath()) and p.IsA(UsdShade.Shader))}
    assert "mix" in ids and "open" in ids
    assert mat.GetPrim().GetAttribute("outputs:mtlx:surface").GetConnections()


def test_remap_targets_keep_only_texture_set_names(tmp_path):
    from rbr2stj import textures
    (tmp_path / "TextureFilenameMap9.ini").write_text(
        "; header\n"
        "track-9_o_textures\\damp\\normal\\a.dds\tdamp\\new\\a.dds\n"
        "track-9_o_textures\\dry\\b.dds\ttrack-13_o_textures\\dry\\b.dds\n")
    assert textures.remap_targets(tmp_path) == ["track-13_o_textures"]


def test_texture_taken_from_another_stage_is_reported(tmp_path):
    import io
    import zipfile
    from PIL import Image
    from rbr2stj import textures
    png = io.BytesIO()
    Image.new("RGB", (2, 2), (9, 9, 9)).save(png, "PNG")
    own, other = tmp_path / "track-1_o_textures.rbz", tmp_path / "track-2_o_textures.rbz"
    with zipfile.ZipFile(own, "w") as z:
        z.writestr("track-1_o_textures/dry/mine.dds", png.getvalue())
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("track-2_o_textures/dry/theirs.dds", png.getvalue())
    src = textures.TextureSource([own, other], [], ["track-1_o_textures"], base="track-1_o")
    exp = textures.TextureExporter(src, tmp_path / "out")
    assert exp.export("mine.tga") and exp.export("theirs.tga")
    assert list(exp.borrowed) == ["theirs.tga"]
    assert "theirs.tga" in exp.summary()["from_other_sets"]


def test_game_path_rules_remap_and_wear(tmp_path):
    import zipfile
    from rbr2stj import textures
    z = tmp_path / "track-5_o_textures.rbz"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("track-5_o_textures/damp/new/road.dds", b"road")          # a ground texture: only damp/new exists
        zf.writestr("track-5_o_textures/damp/sign.dds", b"sign")
        zf.writestr("track-5_o_textures/damp/new/moved.dds", b"moved")
    (tmp_path / "TextureFilenameMap5.ini").write_text(
        "; c\ntrack-5_o_textures\\dry\\old.dds\tdamp\\new\\moved.dds\n", encoding="latin-1")
    src = textures.for_classic(tmp_path, "track-5", "O", ground={"road"})
    assert src.rsf_remap == {"track-5_o_textures/dry/old.dds": "track-5_o_textures/damp/new/moved.dds"}
    for req, name in (("ROAD.tga", "road.dds"), ("SIGN.tga", "sign.dds"), ("OLD.tga", "moved.dds")):
        e = src.find(req)
        assert e is not None and e.via == "path" and e.name.endswith(name), req
