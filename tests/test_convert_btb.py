"""BTB stage folder -> IR -> STJ package, on the synthetic folder."""
import json
import math

import pytest

from rbr2stj import cli
from rbr2stj.convert import Options
from rbr2stj.convert.btb import convert_btb, d3d_to_stj, transform_point
from rbr2stj.errors import Rbr2StjError
from rbr2stj.writers import package
from rbr2stj.writers.usd_mesh import decompose_d3d
from tests.fixtures import build


@pytest.fixture(scope="module")
def stage(btb_stage):
    return convert_btb(btb_stage)


@pytest.fixture(scope="module")
def pkg(tmp_path_factory, btb_stage):
    out = tmp_path_factory.mktemp("btbpkg")
    s = convert_btb(btb_stage)
    return package.write_package([s], out, Options(), validate=False), s


def doc_of(p):
    return json.loads(p.json_path.read_text(encoding="utf-8"))


def test_info_and_timing(stage):
    assert stage.info.name == "Synthetic BTB" and stage.info.surface == "gravel" and stage.kind == "rbr-btb"
    t = stage.timing
    assert (t.start, t.finish, t.stop, t.splits) == (30.0, 690.0, 705.0, [240.0])
    notes = [n["call"] for n in stage.notes]
    assert notes == ["medium left", "medium right long", "ford"]            # tags 21..24 are timing, P50 beyond count ignored


def test_spawn_frame_conversion(stage):
    # spawn.ini m12 m13 m14 = D3D (5, 0.15, 0) -> STJ (5, 0, 0.15); angleZ 1.5708 -> heading pi/2 - 1.5708 ~ 0
    assert stage.spawn.position == pytest.approx((5.0, 0.0, 0.15)) and stage.spawn.heading == pytest.approx(0.0, abs=1e-3)


def test_frame_helpers():
    assert d3d_to_stj((1, 2, 3)) == (1, 3, 2)
    m = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 20, 30, 1)
    assert transform_point(m, (1, 2, 3)) == (11, 22, 33)             # row vector * M, translation in elements 12..14
    # D3D matrix rotating +x towards +z (a heading change in the horizontal plane) must become a STJ yaw about +Z
    c, s = math.cos(0.5), math.sin(0.5)
    m = (c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1)
    pos, q, sc, flag = decompose_d3d(m)
    w, x, y, z = q
    yaw = 2 * math.atan2(z, w)
    assert abs(x) < 1e-9 and abs(y) < 1e-9 and abs(yaw) == pytest.approx(0.5, abs=1e-6) and sc == pytest.approx((1, 1, 1))
    assert not flag
    # the local +x axis maps to STJ direction (cos, sin(yaw)...): rotate (1,0,0) by q and compare with (c, s, 0) in STJ
    # (D3D (c, 0, s) -> STJ (c, s, 0))
    vx = (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y))
    assert vx == pytest.approx((c, s, 0.0), abs=1e-9)
    # scale and mirror detection
    m = (2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 0, 0, 0, 1)
    assert decompose_d3d(m)[2] == pytest.approx((2, 4, 3))           # (|row0|, |row2|, |row1|) = STJ (x, y, z)
    assert decompose_d3d((-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))[3] is True


def test_collision_mesh_from_visible_collisionmodel_0(stage, pkg):
    c = stage.collision
    assert len(c.faces) == 6 and len(c.points) == 12                  # 3 clones x one quad; the Visible=0 object is absent
    assert stage.diagnostics["collision"] == {"objects": 1, "triangles": 6, "vertices": 12}
    # ground tile clone 1: D3D (0,0,-20) quad 40 x 40 -> STJ x 0..40, y -20..20, z 0
    xs = sorted({round(p[0], 3) for p in c.points[:4]})
    ys = sorted({round(p[1], 3) for p in c.points[:4]})
    assert xs == [0.0, 40.0] and ys == [-20.0, 20.0]
    # handedness flip reverses the winding, so the ground faces UP (+z normal)
    for a, b, cc in c.faces:
        pa, pb, pc = c.points[a], c.points[b], c.points[cc]
        nz = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        assert nz > 0


def test_btb_surface_from_mat_ini_via_texture_name(stage):
    st = stage.collision.states[0]
    assert st.palette == [17] and len(st.layers) == 6 and all(lay == (0, 0, 0) for lay in st.layers)
    assert all(w == (1.0, 0.0) for w in st.weights)


def test_surface_map_has_no_condition_for_btb(pkg):
    d = doc_of(pkg[0])
    maps = d["surfaces"]["maps"]
    assert len(maps) == 1 and "when" not in maps[0]
    assert maps[0]["palette"] == ["rbr_gravel_fine_shallow_dry"] and maps[0]["weights"]["weights"] == "stj_surf_weights"


def test_surface_lookup_uses_uv_cells_and_per_corner_blend(tmp_path):
    """A face whose three vertices hit different `mat.ini` cells gets three layers and per-corner weights."""
    from rbr2stj.convert.btb import mesh_faces_surfaces
    from rbr2stj.readers import btb, xfile
    folder = build.build_btb_stage(tmp_path / "b")
    t = btb.open_folder(folder)
    t.map_rows[0][0] = [10, 20, 30] + [10] * 13                           # cells 0, 1, 2 differ
    # u * 15: 0 -> cell 0, 1/15 -> cell 1, 2/15 -> cell 2 ; u = -0.5 -> int(-7.5) = -7 & 15 = 9 (C truncation then low nibble)
    mesh = xfile.Mesh([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [(0, 1, 2)], [(0.0, 0.0), (1 / 15 + 1e-6, 0.0), (2 / 15 + 1e-6, 0.0)],
                      ["TexDir\\Materials\\road"], [0])
    (ids, ws), = mesh_faces_surfaces(t, mesh)
    assert sorted(ids) == [10, 20, 30] and sorted(ws) == [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)]
    mesh.uvs = [(-0.5, 0.0)] * 3
    (ids, ws), = mesh_faces_surfaces(t, mesh)
    assert ids[0] == t.map_rows[0][0][9]                                    # u*15 = -7.5 -> -7 -> low nibble 9
    mesh.uvs = None
    (ids, ws), = mesh_faces_surfaces(t, mesh)
    assert ids[0] == 10                                                    # no UVs: cell 0


def test_box_colliders_become_static_box_props(stage, pkg):
    assert len(stage.props) == 1 and stage.props[0].name == "box_1"
    box = stage.props[0]
    # colBox "4, 2, 3": x +-2, y +-1.5 (STJ axes), z from -0.05 * 2 to 2
    xs = sorted({round(v[0], 3) for v in box.vertices})
    ys = sorted({round(v[1], 3) for v in box.vertices})
    zs = sorted({round(v[2], 3) for v in box.vertices})
    assert xs == [-2.0, 2.0] and ys == [-1.5, 1.5] and zs == [-0.1, 2.0]
    d = doc_of(pkg[0])
    proto = d["scenery"]["prototypes"]["box_1"]
    assert proto["class"] == "prop" and proto["collision"] == "mesh"
    assert d["scenery"]["instances"][0]["transforms"] == [[100.0, 8.0, 0.0, 0.0]]       # D3D (100, 0, 8) -> STJ (100, 8, 0)


def test_visual_meshes_are_instanced_not_expanded(pkg):
    from pxr import Usd, UsdGeom
    p, s = pkg
    d = doc_of(p)
    vis = next(m for m in d["geometry"]["meshes"] if m["id"] == "visual")
    assert vis["roles"] == ["visual"]
    stg = Usd.Stage.Open(str(p.folder / vis["uri"]))
    assert UsdGeom.GetStageUpAxis(stg) == "Z"
    veg = next(m for m in d["geometry"]["meshes"] if m["id"] == "vegetation")       # grass-textured objects with many placements
    vstg = Usd.Stage.Open(str(p.folder / veg["uri"]))
    assert any(pp["class"] == "grass" for pp in d["scenery"]["prototypes"].values())
    assert any(i.get("usd", {}).get("mesh_ref") == "vegetation" for i in d["scenery"]["instances"])
    inst = {pr.GetPath().pathString: pr for st in (stg, vstg) for pr in st.Traverse() if pr.IsA(UsdGeom.PointInstancer)}
    stg = vstg if any("ground" in k for k in {pr.GetPath().pathString for pr in vstg.Traverse()}) else stg
    ground = next(v for k, v in inst.items() if "ground" in k)
    pi = UsdGeom.PointInstancer(ground)
    pos = pi.GetPositionsAttr().Get()
    assert len(pos) == 3 and list(map(tuple, pos))[1] == pytest.approx((40.0, -20.0, 0.5))      # D3D (40, 0.5, -20) -> STJ
    cone = next(v for k, v in inst.items() if "cone" in k)
    assert len(UsdGeom.PointInstancer(cone).GetPositionsAttr().Get()) == 2
    names = [pr.GetPath().pathString for pr in stg.Traverse()]
    assert not any("hidden" in n for n in names)                                                  # Visible=0: absent
    # LOD in/out kept on the instancer, texture NAME on the material, textures copied byte for byte
    assert ground.GetAttribute("stj:lodOut").Get() == 2679.0
    mats = [pr for pr in stg.Traverse() if pr.GetName().startswith("TexDir")]
    road = next(m for m in mats if m.GetAttribute("stj:sourceName").Get().endswith("road"))
    assert road.GetAttribute("stj:diffuseTexture").Get() == "road.dds" and road.GetAttribute("stj:btbEffect").Get() == "BTB_Diffuse"
    assert sorted(f.name for f in (p.folder / "textures").glob("*.dds")) == ["grass.dds", "road.dds"]   # copied unchanged


def test_x_btb_summary_and_lost(pkg):
    d = doc_of(pkg[0])
    objs = d["x_btb"]["objects"]
    assert [o["file"] for o in objs] == ["ground", "box", "hidden", "cone"]
    assert objs[0]["lod_out"] == 2679.0 and objs[0]["xform_count"] == 3 and objs[2]["visible"] is False
    assert d["x_btb"]["materials"]["texdir\\materials\\road"]["diffuse"] == "road.dds"
    cf = d["_metadata"]["provenance"]["converted_from"][0]
    assert cf["format"] == "rbr-btb" and d["_metadata"]["source_format"] == "RBR/BTB"
    assert d["_metadata"]["licence"]["redistributable"] is False
    lost = " ".join(cf["lost"]).lower()
    assert "weather and sky" in lost and "shaders" in lost and "grip" in lost
    assert d["track_info"]["primary_surface"] == "gravel"


def test_missing_mesh_is_reported_not_fatal(tmp_path):
    import zipfile
    folder = build.build_btb_stage(tmp_path / "b")
    with zipfile.ZipFile(folder / "Objects.zip") as z:
        keep = {n: z.read(n) for n in z.namelist() if "cone" not in n}
    (folder / "Objects.zip").unlink()
    with zipfile.ZipFile(folder / "Objects.zip", "w") as z:
        for n, b in keep.items():
            z.writestr(n, b)
    st = convert_btb(folder)
    assert st.diagnostics["skipped_objects"] == {"mesh missing in Objects.zip or unreadable": 1}
    assert any("skipped" in l for l in st.lost)


def test_bad_xform_length_is_reported(tmp_path):
    import zipfile
    folder = build.build_btb_stage(tmp_path / "b")
    with zipfile.ZipFile(folder / "Objects.zip") as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["Objects/ground.xform"] += b"\0" * 7
    (folder / "Objects.zip").unlink()
    with zipfile.ZipFile(folder / "Objects.zip", "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    st = convert_btb(folder)
    assert any("bad .xform" in k for k in st.diagnostics["skipped_objects"])


def test_no_geometry_option(btb_stage, tmp_path):
    st = convert_btb(btb_stage, Options(no_geometry=True))
    p = package.write_package([st], tmp_path / "ng", validate=False)
    d = doc_of(p)
    assert "geometry" not in d and "scenery" not in d and "reference" in d


def test_not_a_btb_folder(tmp_path):
    with pytest.raises(Rbr2StjError, match="not a BTB"):
        convert_btb(tmp_path)


stj_tools = package.find_stj_tools()


@pytest.mark.skipif(stj_tools is None, reason="STJ tools not found")
def test_btb_package_passes_strict_validation(pkg):
    code, text = package.run_validate(pkg[0].root, stj_tools)
    assert code == 0, text


def test_cli_btb_and_info(btb_stage, tmp_path, capsys):
    rc = cli.main(["btb", str(btb_stage), "-o", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 0 and "synthetic_btb.usda" in out and "validate --strict: exit 0" in out
    assert cli.main(["info", str(btb_stage)]) == 0
    assert "BTB stage 'Synthetic BTB'" in capsys.readouterr().out
