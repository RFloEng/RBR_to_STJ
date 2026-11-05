"""P6 (stretch): classic .lbs visual geometry -> a USD visual layer."""
import json

import pytest

from rbr2stj.convert import Options
from rbr2stj.convert.classic import convert_classic
from rbr2stj.readers import lbs
from rbr2stj.writers import package
from tests.fixtures import build

stj_tools = package.find_stj_tools()


@pytest.fixture(scope="module")
def stage(classic_stage):
    return convert_classic(classic_stage.folder, Options(variant="M"))


def test_lbs_geometry_reader(classic_stage):
    parsed = lbs.parse((classic_stage.folder / "track-101_M.lbs").read_bytes())
    assert parsed.chunk_kinds[0] == (1, 0) and len(parsed.geometry) == 1
    blk = parsed.geometry[0]
    assert [c.draw_list for c in blk.chunks] == [2, 3]          # the chunk on the undecoded slot 1 is skipped
    assert parsed.skipped_chunks == 1
    v = blk.views[5]
    assert v.positions[1] == (40.0, 0.0, 0.0) and v.uvs[2] == (1.0, 1.0) and v.colours[0] == 0xFF102030
    assert blk.views[1] is None and blk.chunks[0].textures == [0, 1] and blk.chunks[0].triangles == 2


def test_btb_style_stub_geometry_is_empty():
    stub = build.write_chunk((1, 0), (0).to_bytes(4, "little"))
    assert lbs.parse(stub).geometry == []


def test_visual_layer_frame_winding_and_materials(tmp_path, stage):
    from pxr import Usd, UsdGeom
    p = package.write_package([stage], tmp_path / "o", validate=False)
    d = json.loads(p.json_path.read_text(encoding="utf-8"))
    vis = next(m for m in d["geometry"]["meshes"] if m["id"] == "visual")
    assert vis["roles"] == ["visual"]
    stg = Usd.Stage.Open(str(p.folder / vis["uri"]))
    mesh = UsdGeom.Mesh(stg.GetPrimAtPath("/visual/block0/view5"))
    pts = mesh.GetPointsAttr().Get()
    assert [tuple(x) for x in pts][2] == (40.0, 40.0, 0.0)               # lbs (40, 0, 40) -> STJ (x, z, y) = (40, 40, 0)
    idx = mesh.GetFaceVertexIndicesAttr().Get()
    assert len(idx) == 6                                                  # far-LOD chunk (draw list 3) not written
    a, b, c = (pts[i] for i in idx[:3])
    assert (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) > 0     # reversed winding: faces up
    pv = {q.GetPrimvarName() for q in UsdGeom.PrimvarsAPI(mesh).GetPrimvars()}
    assert {"st", "displayColor"} <= pv
    sub = UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(mesh))
    assert len(sub) == 1
    from pxr import UsdShade
    m = UsdShade.MaterialBindingAPI(sub[0].GetPrim()).ComputeBoundMaterial()[0].GetPrim()
    assert m.GetAttribute("stj:diffuseTexture").Get() == "road" and m.GetAttribute("stj:textureRequest").Get() == "dry\\new\\road.tga"
    assert not list(p.folder.rglob("*.dds")) and not list(p.folder.rglob("*.tga"))      # never embedded


def test_lost_mentions_what_p6_leaves_out(tmp_path, stage):
    p = package.write_package([stage], tmp_path / "o", validate=False)
    lost = " ".join(json.loads(p.json_path.read_text(encoding="utf-8"))["_metadata"]["provenance"]["converted_from"][0]["lost"])
    assert "far-LOD" in lost and "domes" in lost and "1 render chunk(s) use a vertex layout" in lost


@pytest.mark.skipif(stj_tools is None, reason="STJ tools not found")
def test_package_with_visual_layer_passes_strict_validation(tmp_path, stage):
    p = package.write_package([stage], tmp_path / "o", validate=False)
    code, text = package.run_validate(p.root, stj_tools)
    assert code == 0, text
