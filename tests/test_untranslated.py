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
