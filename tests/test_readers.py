"""Round trips: test-only writers (tests/fixtures/build.py) -> readers. Edge cases from the reRBR notes."""
import math

import pytest

from rbr2stj import guard, ini
from rbr2stj.errors import FormatError, ProtectedFileError
from rbr2stj.readers import btb, col, dls, fnc, lbs, mat, rsf, stage_files, surfaces_table, trk, xfile, xform
from tests.fixtures import build


def read(stage, ext, letter="M"):
    return (stage.folder / f"track-101_{letter}.{ext}").read_bytes()


def test_trk_roundtrip(classic_stage):
    t = trk.parse(read(classic_stage, "trk"))
    assert len(t.driveline) == len(classic_stage.points)
    assert t.driveline[0].position == pytest.approx(classic_stage.points[0])
    assert t.driveline[-1].distance == pytest.approx(classic_stage.length, rel=1e-5)
    assert [m.name for m in t.meshes] == ["CONE.KSH", "SIGN.KSH", "HO_BUSH01.KSH"]
    cone = t.meshes[0]
    assert cone.kind == 1 and cone.dynamic == 1 and cone.shape[0] == "box" and len(cone.placements) == 3
    assert t.meshes[2].faces[0][0] == 0xFF            # spectator tag kept
    assert t.meshes[2].shape[0] == "sphere" and t.meshes[2].material == 165


def test_trk_truncated_is_a_clear_error(classic_stage):
    data = read(classic_stage, "trk")
    with pytest.raises(FormatError, match="truncated"):
        trk.parse(data[:-5])
    with pytest.raises(FormatError):
        trk.parse(b"")        # no driveline chunk


def test_dls_markers_and_triggers(classic_stage):
    d = dls.parse(read(classic_stage, "dls"))
    assert d.set_names == ["Camera", "Driveline"]       # Driveline found by name, not by position
    m = dls.markers(d)
    assert (m.start, m.finish, m.end) == (30.0, 690.0, 705.0)
    assert m.splits == [240.0, 480.0]
    assert [(k.tag, k.distance) for k in m.notes] == [(t, d_) for t, _f, d_ in build.NOTES]
    assert [k.distance for k in d.keys] == sorted(k.distance for k in d.keys)
    assert [(t.object, t.distance, t.lead) for t in d.triggers] == [build.TRIGGER]
    assert d.named_triggers == [("start_animation2", 7)]
    assert d.spheres == [(100.0, 0.0, 1.0, 5.0)]


def test_dls_splits_capped_at_16_and_last_start_wins():
    keys = [(0x17, 0, float(i)) for i in range(20)] + [(0x15, 0, 5.0), (0x15, 0, 9.0)]
    m = dls.markers(dls.parse(build.write_dls(keys)))
    assert len(m.splits) == 16 and m.start == 9.0 and m.finish is None


def test_dls_errors():
    with pytest.raises(FormatError, match="MINAATAD"):
        dls.parse(b"X" * 80)
    with pytest.raises(FormatError, match="shorter"):
        dls.parse(b"MINAATAD")
    bad = bytearray(build.write_dls([(0x15, 0, 1.0)]))
    bad[16:20] = (len(bad) + 100).to_bytes(4, "little")   # table 0 offset outside the file
    with pytest.raises(FormatError):
        dls.parse(bytes(bad))


def test_mat_select_by_words(classic_stage):
    m = mat.parse(read(classic_stage, "mat"))
    assert len(m.entries) == 9 and m.tail == b"CMAP\0\0\0\0"
    i = mat.select(m.entries, "worn", "wet")
    assert m.entries[i].name == "testrally standard wet worn"
    assert m.entries[mat.select(m.entries, "normal", "damp")].name == "testrally standard damp normal"
    assert mat.select(m.entries[:2], "worn", "wet") is None
    # sampler: nibble -> cell: 15 clamps to cell 15, 0 -> cell 0
    e = m.entries[0]
    assert mat.surface_at(e, 1, 0) == 17 and mat.surface_at(e, 1, 15) == 105 and mat.surface_at(e, 5, 3) == 0


def test_mat_zero_run_entry_is_kept():
    m = mat.parse(build.write_mat([("a dry new", []), ("a dry worn", [bytes(256)])]))
    assert mat.select(m.entries, "new", "dry") == 0 and m.entries[0].runs == []


def test_fnc_roundtrip(classic_stage):
    f = fnc.parse(read(classic_stage, "fnc"))
    assert [g.fence_type for g in f.groups] == [0, 2] and [len(g.posts) for g in f.groups] == [6, 4]
    assert f.textures == ["fence_tape", "fence_net"]
    assert fnc.parse(build.write_fnc([])).groups == []
    with pytest.raises(FormatError, match="magic"):
        fnc.parse(b"\x03\0\0\0\0\0\0\0")


def test_lbs_start_and_objects(classic_stage):
    parsed = lbs.parse(read(classic_stage, "lbs"))
    assert parsed.start.position == pytest.approx((5.0, 0.15, 0.0))
    assert parsed.start.rotation[1] == pytest.approx(math.pi / 2)
    assert [o.name for o in parsed.animation_objects] == ["marshal01", "bird01"]
    assert parsed.animation_objects[1].scripts == ("bird.LUA", "bird2.LUA")


def test_col_roundtrip(classic_stage):
    c = col.parse(read(classic_stage, "col"))
    assert len(c.cells) == 2 and c.superseded_cells == 0
    assert all(len(cell.triangles) > 0 for cell in c.cells)
    assert len(c.puddles) == 1 and len(c.deep_water) == 2
    assert c.puddles[0][0] == pytest.approx((258.0, 140.0, 1.0))
    t0 = c.cells[0].triangles
    assert {tuple(t.mat) for t in t0} == {(0, 0), (0, 1)}
    assert any(31 in t.attr_a for t in t0)
    assert c.brake_wall and len(c.brake_wall.points) == 12
    flags = {w.index: (w.sensor, w.respawn) for w in c.brake_wall.items}
    assert flags == {0: (False, False), 1: (False, False), 2: (True, False), 3: (False, True), 4: (True, True)}


def test_col_later_cell_wins_on_same_graft():
    c0 = {"vertices": [(0, 0, 0), (1, 0, 0), (0, 1, 0)], "graft": 0x01000000,
          "triangles": [dict(v=(0, 1, 2), attr_a=(0, 0, 0), attr_b=(0, 0, 0), mat=(0, 0), nib=((0, 0, 0), (0, 0, 0)))]}
    c1 = dict(c0, vertices=[(5, 5, 5), (6, 5, 5), (5, 6, 5)])
    parsed = col.parse(build.write_col([c0, c1]))
    assert parsed.superseded_cells == 1 and len(parsed.cells) == 1
    assert parsed.cells[0].vertices[0] == (5, 5, 5)


def test_col_errors(classic_stage):
    data = bytearray(read(classic_stage, "col"))
    with pytest.raises(FormatError, match="magic"):
        col.parse(b"NOPE" + bytes(60))
    with pytest.raises(FormatError):
        col.parse(bytes(data[:len(data) // 2]))
    bad = bytearray(data)
    bad[16 + 1] = 9                                 # root is not type 4/3
    with pytest.raises(FormatError, match="not type 4/3"):
        col.parse(bytes(bad))


def test_col_cell_without_vertices_is_skipped():
    empty = {"vertices": [], "graft": 0x01000000, "triangles": []}
    assert col.parse(build.write_col([empty])).cells == []


def test_rsf_info_and_weathers(classic_stage):
    info = rsf.read_folder(classic_stage.folder)
    assert info.id == 101 and info.name == "Test Stage" and info.surface == "gravel" and info.length_km == 0.73
    assert info.default_wetness == 0 and info.country == "ES" and info.track_name == "track-101"
    names = [w.section for w in info.weathers]
    assert "101M_hazy_clear" in names and "101O_E_hazy_partcloud" in names
    assert "101M_bogus_weather" not in names
    w = next(w for w in info.weathers if w.section.startswith("101O_E"))
    assert (w.letter, w.cloud_letter, w.preset, w.cloud) == ("O", "E", "hazy", "partcloud")
    assert rsf.parse_weather(101, "102M_hazy_clear") is None


def test_surface_table():
    assert surfaces_table.name(17) == "Gravel_Fine_Shallow_Dry" and surfaces_table.stj_id(157) == "rbr_water_surface"
    assert surfaces_table.classify(105) == ("grass", None)
    assert surfaces_table.classify(121) == ("other", "bendable")
    assert surfaces_table.classify(62) == ("snow", "rigid")
    assert surfaces_table.name(250) == "Unknown_250"


# ── protection ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("head", [b"\x01\xa0\x00\x00", b"\x01\xa1\x00\x00", b"\x21\x19\x19\x19"])
def test_protected_files_are_refused(head, tmp_path):
    p = tmp_path / "track-1_M.col"
    p.write_bytes(head + b"rest of an encrypted file")
    with pytest.raises(ProtectedFileError, match="encrypted by RallySimFans"):
        guard.read_file(p)
    assert not guard.is_protected(b"OC7R")


def test_encrypted_stage_file_is_refused_when_opening_a_stage(tmp_path):
    folder = tmp_path / "s"
    build.build_classic_stage(folder, with_rsf=False)
    (folder / "track-101_M.col").write_bytes(b"\x01\xa0\x00\x00" + bytes(100))
    s, _ = stage_files.open_stage(folder)
    with pytest.raises(ProtectedFileError):
        s.read("col")


# ── ini ─────────────────────────────────────────────────────────────────────

def test_ini_first_match_and_case_insensitive():
    s = ini.parse(b"\xef\xbb\xbf; c\r\n[Info]\r\nName = A\r\nname=B\r\n[INFO]\r\nName=C\r\n[x] trailing\r\nk=v")
    assert ini.get(s, "info", "NAME") == "A" and ini.find_section(s, "x").get("k") == "v"


def test_ini_lossy_bytes():
    s = ini.parse(b"[a]\r\nn=Test\xe9\xff\r\n")
    assert ini.get(s, "a", "n").startswith("Test") and "�" in ini.get(s, "a", "n")


def test_c_locale_scans():
    assert ini.scan_float("2679,576") == 2679.0 and ini.scan_float("  -1.5e2x") == -150.0 and ini.scan_float("abc") is None
    assert ini.scan_col_box("0,15, 1, 0,15") == (0.0, 15.0, 1.0)
    assert ini.scan_col_box("1, 2") is None
    assert ini.decimal_floats("0,4235294 0,5") == pytest.approx([0.4235294, 0.5])
    assert ini.data_cells("1 2 x3 4a")[:4] == [1, 2, 0, 4] and ini.data_cells("") == [0] * 16
    assert ini.data_cells("300")[0] == 300 & 0xFF


# ── BTB ──────────────────────────────────────────────────────────────────────

def test_btb_folder(btb_stage):
    t = btb.open_folder(btb_stage)
    assert t.name == "Synthetic BTB" and t.physics == "gravel" and t.splashscreen == "splash.jpg"
    assert t.author.startswith("Test") and t.length_km is None          # "0,73": a decimal comma is not read as a float
    assert len(t.driveline) == len(build.centreline())
    c = build.centreline()
    assert t.driveline[1].distance == pytest.approx(math.dist(c[0], c[1]), rel=1e-4)
    assert [n.tag for n in t.notes] == [3, 8, 17, 21, 22, 23, 24]       # P50 beyond count ignored
    assert t.spawn == (5.0, 0.15, 0.0) and t.spawn_angle_z == pytest.approx(1.5708)
    assert t.sun_direction == pytest.approx((-0.5, -0.7, -0.5))


def test_btb_mat_ini_quirks(btb_stage):
    t = btb.open_folder(btb_stage)
    assert len(t.map_rows) == 2
    assert t.map_rows[0][0] == [17] * 16
    # glued "69[MAP1]": MAP1 is not a section -> reads zeros; the glued token still reads 69 (atoi) as the last cell
    assert t.map_rows[0][15][15] == 69 and t.map_rows[1] == [[0] * 16 for _ in range(16)]
    assert t.map_textures[0] == "road.dds"                              # duplicate [MAP0]: first wins


def test_btb_objects_and_materials(btb_stage):
    t = btb.open_folder(btb_stage)
    assert [o.collision_model for o in t.objects] == [0, 2, 0, -1]
    assert t.objects[0].lod_out == 2679.0 and t.objects[1].col_box == (4.0, 2.0, 3.0)
    assert t.objects[2].visible is False
    m = t.materials["texdir\\materials\\road"]
    assert m.effect == "BTB_Diffuse" and m.diffuse.endswith("road.dds")
    assert "texdir\\materials\\grass" in t.materials                    # MAT_1 = grass-blend2 -> section "grass"
    assert t.map_index_for_texture("road") == 0 and t.map_index_for_texture("nothing") == 0


def test_btb_meshes_and_xforms(btb_stage):
    t = btb.open_folder(btb_stage)
    g = t.mesh("ground")
    assert len(g.vertices) == 4 and len(g.triangles) == 2 and g.uvs and g.material_names == ["TexDir\\Materials\\road"]
    assert t.mesh("box").uvs is None                                    # .x without UVs
    assert len(t.placements("ground")) == 3 and t.placements("missing") is None and t.mesh("missing") is None


def test_xform_misaligned_is_an_error():
    with pytest.raises(FormatError, match="multiple"):
        xform.parse(b"\0" * 65)
    assert len(xform.parse(b"\0" * 128)) == 2


def test_xfile_errors_and_roundtrip():
    m = xfile.Mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], None, ["T"], [0])
    for tpl in (True, False):
        r = xfile.parse(xfile.write_binary(m, with_templates=tpl))
        assert r.vertices == m.vertices and r.triangles == m.triangles and r.material_names == ["T"]
    with pytest.raises(FormatError, match="xof"):
        xfile.parse(b"nope" * 8)
    with pytest.raises(FormatError, match="bin"):
        xfile.parse(b"xof 0303txt 0032" + b"\0" * 8)
    with pytest.raises(FormatError):
        xfile.parse(xfile.write_binary(m)[:-9])


def test_btb_folder_checks(tmp_path):
    with pytest.raises(Exception, match="not a BTB"):
        btb.open_folder(tmp_path)
