"""A BTB stage and the same road run the other way as one STJ 0.4 package."""
import json
import shutil
import sys
import zipfile
from collections import Counter

import pytest

from rbr2stj import cli
from rbr2stj.convert import Options, btb_layouts
from rbr2stj.convert.btb import convert_btb
from rbr2stj.readers import xform
from rbr2stj.writers import package
from tests.fixtures import build


def _ident(x, y, z):
    return (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1)


def _reverse_driveline(folder):
    pts = build.centreline()[::-1]
    dist, tans = build.stored_distance(pts), build.tangents(pts)
    text = f"[DRIVELINE]\r\ncount={len(pts)}\r\n"
    for i, (p, t, d) in enumerate(zip(pts, tans, dist)):
        text += f"K{i}=" + ", ".join(f"{v:.5f}" for v in (*p, *t, d, 0)) + "\r\n"
    (folder / "driveline.ini").write_bytes(text.encode())


def _rezip(path, changes):
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files.update(changes)
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """`Synthetic` and `Synthetic II`: the road reversed; one ground tile, the box collider and one cone moved."""
    root = tmp_path_factory.mktemp("pair")
    a = build.build_btb_stage(root / "Synthetic")
    b = root / "Synthetic II"
    shutil.copytree(a, b)
    _reverse_driveline(b)
    ini = (b / "track.ini").read_bytes().replace(b"name=Synthetic BTB", b"name=Synthetic BTB II")
    (b / "track.ini").write_bytes(ini)
    _rezip(b / "Objects.zip", {
        "Objects/ground.xform": xform.pack([_ident(0, 0, -20), _ident(40, 0.5, -20), _ident(80, 3.0, -20)]),
        "Objects/box.xform": xform.pack([_ident(104, 0, 8)]),
        "Objects/cone.xform": xform.pack([_ident(50, 0, 6), _ident(58, 0, 6)])})
    return a, b


@pytest.fixture(scope="module")
def merged(pair, tmp_path_factory):
    a, b = pair
    st = convert_btb(a)
    m, why = btb_layouts.merge_reverse(st, b, Options())
    assert m, why
    st.merge = m
    return package.write_package([st], tmp_path_factory.mktemp("merged"), Options(), validate=False), m


def _doc(p):
    return json.loads(p.json_path.read_text(encoding="utf-8"))


def _core():
    tools = package.find_stj_tools()
    if tools is None:
        pytest.skip("STJ tools not found")
    sys.path.insert(0, str(tools))
    import stj_core
    if not hasattr(stj_core, "default_document"):
        pytest.skip("STJ tools older than 0.4")
    return stj_core


def test_reverse_folder_is_found_by_name(pair):
    a, b = pair
    assert btb_layouts.find_btb_reverse_folders(a) == [b.resolve()]
    assert btb_layouts.find_btb_reverse_folders(b) == []
    for name in ("Hill II", "Hill R", "Hill Rever", "Hill Reversed"):
        assert btb_layouts.is_reverse_name(name) and btb_layouts._key(name) == "hill"
    assert not btb_layouts.is_reverse_name("Hill") and not btb_layouts.is_reverse_name("SSS3R2")


def test_a_stage_that_is_not_reversed_stays_separate(pair, tmp_path):
    a, _b = pair
    same_way = tmp_path / "Synthetic II"
    shutil.copytree(a, same_way)
    st = convert_btb(a)
    m, why = btb_layouts.merge_reverse(st, same_way, Options())
    assert m is None and "backwards" in why and st.layouts == []


def test_triangle_identity_ignores_the_start_corner_but_not_the_winding():
    from rbr2stj.ir import CollisionMesh, SurfaceState
    # face 1 is face 0 started at its second corner (weights follow); face 2 is face 0 wound the other way
    weights = [(1.0, 0.0), (0.0, 1.0), (1.0, 0.0),
               (0.0, 1.0), (1.0, 0.0), (1.0, 0.0),
               (1.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    st = SurfaceState("all", "normal", "dry", [7, 9], [(0, 1, 0)] * 3, weights)
    mesh = CollisionMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2), (1, 2, 0), (0, 2, 1)], [st])
    k = btb_layouts.triangle_keys(mesh)
    assert k[0] == k[1] and k[0] != k[2]


def test_multiset_match():
    assert btb_layouts.match(["a", "a", "b"], ["c", "a"]) == ([0], [1, 2], [0])


def test_each_part_holds_what_it_should(merged):
    _p, m = merged
    assert m.stats["collision_triangles"] == {"common": 4, "forward": 2, "reverse": 2}
    # ground tiles: two identical; the drawn box moved; cones: one identical
    assert m.stats["visual_placements"] == {"common": 3, "forward": 3, "reverse": 3}
    assert m.stats["box_placements"] == {"common": 0, "forward": 1, "reverse": 1}


def test_a_changed_texture_makes_its_models_differ(pair, tmp_path):
    a, b = pair
    c = tmp_path / "Synthetic II"
    shutil.copytree(b, c)
    _rezip(c / "Textures.zip", {"grass.dds": b"DDS changed"})
    st = convert_btb(a)
    m, why = btb_layouts.merge_reverse(st, c, Options())
    assert m, why
    # the cones and the box use the grass texture: none of them is common now, only two ground tiles are
    assert m.stats["visual_placements"] == {"common": 2, "forward": 4, "reverse": 4}
    st.merge = m
    p = package.write_package([st], tmp_path / "out", Options(), validate=False)
    textures = {f.name: f.read_bytes() for f in (p.folder / "textures").iterdir()}
    assert textures == {"grass.dds": b"DDS ", "grass_2.dds": b"DDS changed", "road.dds": b"DDS "}
    assert _doc(p)["layouts"][0]["x_rbr"]["texture_files"]


def test_equal_textures_are_written_once(merged):
    assert sorted(f.name for f in (merged[0].folder / "textures").iterdir()) == ["grass.dds", "road.dds"]


def test_document_declares_0_4_and_the_reverse_layout(merged):
    d = _doc(merged[0])
    assert d["_metadata"]["version"] == "0.4"
    only = {m["id"] for m in d["geometry"]["meshes"] if m.get("layout_only")}
    ids = {m["id"] for m in d["geometry"]["meshes"]}
    assert only == {i for i in ids if i.endswith("_reverse")} and "collision_reverse" in only
    lay, = d["layouts"]
    assert lay["id"] == "reverse" and lay["name"] == "Synthetic BTB II"
    assert set(lay["include_meshes"]) == {i for i in ids if not i.endswith("_forward")}
    assert lay["reference"]["centerline"]["points"][0] == pytest.approx(d["reference"]["centerline"]["points"][-1])
    sources = d["_metadata"]["provenance"]["converted_from"]
    assert [s["source"] for s in sources] == ["Synthetic", "Synthetic II"]


def test_box_placements_follow_their_direction(merged):
    core = _core()
    d = _doc(merged[0])
    sets = [i for i in d["scenery"]["instances"] if "transforms" in i]
    assert sorted(i["with_mesh"] for i in sets) == ["collision_forward", "collision_reverse"]
    assert len({i["prototype"] for i in sets}) == 1                  # one box model, placed differently

    def boxes(doc):
        return [t[:2] for i in doc["scenery"]["instances"] if "transforms" in i for t in i["transforms"]]

    assert boxes(core.default_document(d)) == [[100.0, 8.0]]
    assert boxes(core.layout_document(d, "reverse")) == [[104.0, 8.0]]


def test_root_layer_shows_the_default_layout(merged):
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(merged[0].root))
    prims = {c.name: c for c in layer.GetPrimAtPath("/track").nameChildren}
    assert any(n.endswith("_reverse") for n in prims)
    for name, spec in prims.items():
        assert (spec.HasInfo("active") and not spec.active) == name.endswith("_reverse"), name


def test_blend_hides_the_other_layout(merged):
    from rbr2stj.writers import blend
    assert blend.layout_only_meshes(merged[0].root) and all(
        i.endswith("_reverse") for i in blend.layout_only_meshes(merged[0].root))


def test_validates_strictly(merged):
    _core()
    code, text = package.run_validate(merged[0].root, package.find_stj_tools())
    assert code == 0, text


def test_each_selection_holds_its_source_ground(pair, merged):
    a, b = pair
    _p, m = merged
    for side, source in (("forward", a), ("reverse", b)):
        whole = Counter(btb_layouts.triangle_keys(convert_btb(source).collision))
        parts = sum((Counter(btb_layouts.triangle_keys(m.collision[s])) for s in ("common", side)), Counter())
        assert parts == whole


def test_cli_merges_by_default_and_can_be_told_not_to(pair, tmp_path, capsys):
    a, _b = pair
    assert cli.main(["btb", str(a), "-o", str(tmp_path / "one"), "--no-validate"]) == 0
    assert "layout 'reverse' <- Synthetic II" in capsys.readouterr().out
    assert cli.main(["btb", str(a), "-o", str(tmp_path / "two"), "--no-validate", "--no-layouts"]) == 0
    assert "layout" not in capsys.readouterr().out
    d = json.loads(next((tmp_path / "two").glob("*.stj.json")).read_text(encoding="utf-8"))
    assert "layouts" not in d and d["_metadata"]["version"] == "0.3"
