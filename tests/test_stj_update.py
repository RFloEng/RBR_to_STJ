"""What the updated STJ standard (per-segment tangents, fixed lighting, LOD meshes, baked, assets, engine-driven objects,
engine sound events, visual water) receives from a converted stage."""
import json

import pytest

from rbr2stj.convert import Options
from rbr2stj.convert.classic import convert_classic
from rbr2stj.readers import lbs_extras as X
from rbr2stj.ir import Weather
from rbr2stj.writers import package, stj_doc


@pytest.fixture(scope="module")
def doc(classic_stage, tmp_path_factory):
    st = convert_classic(classic_stage.folder, Options(variant="M"))
    p = package.write_package([st], tmp_path_factory.mktemp("o"), validate=False)
    return json.loads(p.json_path.read_text(encoding="utf-8")), p


def test_tangents_are_per_segment(doc):
    cl = doc[0]["reference"]["centerline"]
    n = len(cl["points"])
    assert "tangents" not in cl and len(cl["tangents_out"]) == n == len(cl["tangents_in"])
    assert cl["tangents_in"][4] == cl["tangents_out"][3]          # a segment ends with the tangent it started with


def test_far_chunks_are_a_lod_mesh_with_baked_light(doc):
    meshes = {m["id"]: m for m in doc[0]["geometry"]["meshes"]}
    assert meshes["visual"]["baked"]["vertex_colour"] == {"primvar": "displayColor", "role": "light"}
    lod = meshes["visual_lod1"]
    assert lod["roles"] == ["lod"] and lod["lod"]["of"] == "visual" and lod["lod"]["level"] == 1


def test_assets_list_every_copied_texture_with_a_hash(doc):
    d, p = doc
    if not d.get("assets"):
        pytest.skip("the synthetic stage has no textures to copy")
    for a in d["assets"]:
        assert (p.folder / a["uri"]).is_file() and len(a["sha256"]) == 64 and a["licence"]["redistributable"] is False


def test_sun_direction_is_negated_and_axes_swapped():
    w = Weather({"SunDir": "-0.653183 -0.054725 -0.755220", "AmbientRed": "0.5", "AmbientGreen": "0.5", "AmbientBlue": "0.5",
                 "DiffuseRed": "0.4", "DiffuseGreen": "0.4", "DiffuseBlue": "0.4"}, "s", "M", None, "x", "y")
    env = stj_doc.weather_env(w)["lighting"]
    assert env["sun"]["direction"] == pytest.approx([0.65318, 0.75522, 0.05472], abs=1e-4)      # towards the sun, Z up
    assert env["sun"]["color"] == [0.4, 0.4, 0.4] and env["ambient"] == {"color": [0.5, 0.5, 0.5], "intensity": 1.0}


def test_sound_spheres_become_engine_event_emitters():
    from rbr2stj.ir import Stage, Info, Line, Timing
    st = Stage("rbr-classic", Info("x"), "x", Line([], None, []), Timing())
    st.x_rbr["sound_spheres"] = [{"position": [1.0, 2.0, 3.0], "radius_word": 100.0}]
    a = stj_doc.audio_section(st)["emitters"][0]
    assert "sound" not in a and a["max_distance"] == 100.0 and "x_rbr" in a


def test_clocks_and_flags_are_engine_driven_objects():
    from rbr2stj.ir import Stage, Info, Line, Timing
    st = Stage("rbr-classic", Info("x"), "x", Line([], None, []), Timing())
    objs = stj_doc.animated_objects(st, {"START_CLOCK": ["/extras/interactive/a_p0"], "BANNER_X": ["/extras/interactive/b_p0"]})
    assert [o["kind"] for o in objs] == ["clock"] and objs[0]["target"]["nodes"] == ["/extras/interactive/a_p0"]
    assert "time_range" not in objs[0] and "x_rbr" in objs[0]
