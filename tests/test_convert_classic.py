"""Classic stage -> IR -> STJ document/package, on the synthetic stage."""
import json
import math
from pathlib import Path

import pytest

from rbr2stj import cli
from rbr2stj.convert import Options
from rbr2stj.convert.classic import convert_classic
from rbr2stj.convert.project import RefLine
from rbr2stj.errors import ProtectedFileError
from rbr2stj.writers import package, stj_doc
from tests.fixtures import build

STAGE_NAME = "Test Stage"


@pytest.fixture(scope="module")
def stage(classic_stage):
    return convert_classic(classic_stage.folder, Options(variant="M"))


@pytest.fixture(scope="module")
def pkg(tmp_path_factory, classic_stage):
    out = tmp_path_factory.mktemp("pkg")
    s = convert_classic(classic_stage.folder, Options(variant="M"))
    return package.write_package([s], out, Options(), validate=False), s


def doc_of(p):
    return json.loads(p.json_path.read_text(encoding="utf-8"))


# ── line, timing, notes (P1) ─────────────────────────────────────────────────

def test_line_chainage_and_tangents(stage, classic_stage):
    assert stage.line.chainage[0] == 0.0 and stage.line.chainage_offset == 0.0
    assert stage.line.chainage[-1] == pytest.approx(classic_stage.length, rel=1e-5)
    assert all(b > a for a, b in zip(stage.line.chainage, stage.line.chainage[1:]))
    assert stage.line.points[0] == pytest.approx(classic_stage.points[0])
    # open question 1: tangents are dP/du over a segment, about one chord long; the game's one tangent per segment is written as tangents_out / tangents_in
    r = stage.diagnostics["tangent_to_chord_ratio"]
    assert 0.8 < r["median"] < 1.25 and len(stage.line.tangents) == len(stage.line.tangents_raw)
    n = len(stage.line.points)
    assert len(stage.line.tangents_in) == n and stage.line.tangents_in[5] == stage.line.tangents[4]


def test_open_question_2_chainage_is_compared_with_xy_and_3d_length(stage):
    c = stage.diagnostics["chainage_vs_geometry"]
    assert c["stored_length"] == pytest.approx(c["xy_length"], rel=0.01)
    assert c["length_3d"] >= c["xy_length"]


def test_nonzero_first_distance_is_shifted(tmp_path):
    build.build_classic_stage(tmp_path / "s", offset=1000.0)
    st = convert_classic(tmp_path / "s")
    assert st.line.chainage_offset == pytest.approx(1000.0) and st.line.chainage[0] == 0.0
    assert st.x_rbr["chainage_offset"] == pytest.approx(1000.0)
    assert (st.timing.start, st.timing.finish, st.timing.stop) == pytest.approx((30.0, 690.0, 705.0), abs=1e-3)
    assert st.timing.splits == pytest.approx([240.0, 480.0], abs=1e-3)
    assert st.raw_notes[0].s == pytest.approx(198.0, abs=1e-3)


def test_timing_and_markers_lie_on_the_line(stage):
    t = stage.timing
    assert (t.start, t.finish, t.stop) == (30.0, 690.0, 705.0) and t.splits == [240.0, 480.0]
    length = stage.line.chainage[-1]
    assert 0 <= t.start < t.splits[0] < t.splits[1] < t.finish <= t.stop <= length
    assert all(0 <= n.s <= length for n in stage.raw_notes)


def test_pacenotes_in_document(pkg):
    p, s = pkg
    notes = doc_of(p)["race"]["pacenotes"]
    assert notes["scale"] == "ascending_1_6" and notes["language"] == "en"
    ns = notes["notes"]
    assert [n["s"] for n in ns] == sorted(n["s"] for n in ns) and len(ns) == 11        # tag 13 skipped
    assert ns[0]["kind"] == "turn" and ns[0]["direction"] == "left" and ns[0]["severity"] == 3
    assert any(n["call"] == "medium right long" for n in ns)
    assert all("distance_to_next" not in n for n in ns)                                  # derived: never written
    assert s.x_rbr["notes_skipped"] == [{"tag": 13, "flags": 0, "s": 610.0, "reason": "distance call (derived by the consumer)"}]


def test_left_bend_gives_left_note_and_handedness_checks_out(stage):
    """Open question 3 (frame): a left bend of the .trk driveline carries a left note, right bend a right note."""
    assert stage.diagnostics["pacenotes"]["direction_vs_geometry"] == {"agree": 2, "disagree": 0, "unclear": 0}


def test_mirrored_stage_is_flagged(tmp_path):
    build.build_classic_stage(tmp_path / "m", mirror=True)
    st = convert_classic(tmp_path / "m", Options(no_geometry=True))
    assert st.diagnostics["pacenotes"]["direction_vs_geometry"]["disagree"] == 2


def test_triggers_and_extras_kept_in_x_rbr(stage):
    x = stage.x_rbr
    assert x["triggers"] == [{"object": 7, "s": 500.0, "lead": 2.5}]
    assert x["named_triggers"] == [{"name": "start_animation2", "object": 7}]
    assert [o["name"] for o in x["animation_objects"]] == ["marshal01", "bird01"]
    assert x["textures"] == [r"dry\new\road.tga", "verge.tga"] and x["fence_textures"] == ["fence_tape", "fence_net"]
    assert x["sound_spheres"][0]["position"] == [100.0, 0.0, 1.0]


def test_spawn_pose_from_lbs(stage):
    # lbs (5, 0.15, 0) -> STJ (5, 0, 0.15); yaw pi/2 about lbs Y (forward +x in lbs) -> heading 0
    assert stage.spawn.position == pytest.approx((5.0, 0.0, 0.15)) and stage.spawn.heading == pytest.approx(0.0, abs=1e-6)


def test_metadata_provenance_and_lost(pkg):
    d = doc_of(pkg[0])
    m = d["_metadata"]
    assert m["licence"]["redistributable"] is False and m["source_format"] == "RBR" and m["version"] == "0.3"
    cf = m["provenance"]["converted_from"][0]
    assert m["provenance"]["type"] == "converted" and cf["format"] == "rbr-classic" and cf["tool"] == "rbr2stj"
    lost = " | ".join(cf["lost"])
    for word in ("Grip", "NGP ruts", "Baked vertex light", "Lua", "Particle", "Visibility", "classic visual geometry"):
        assert word.lower() in lost.lower()


def test_track_info_from_rsf(pkg):
    ti = doc_of(pkg[0])["track_info"]
    assert ti["name"] == STAGE_NAME and ti["type"] == "point_to_point" and ti["primary_surface"] == "gravel"
    assert ti["length"] == pytest.approx(730.0) and ti["country"] == "ES"


# ── collision and surfaces (P2) ──────────────────────────────────────────────

def test_collision_mesh_in_usd(pkg):
    from pxr import Usd, UsdGeom
    p, s = pkg
    d = doc_of(p)
    uri = next(m["uri"] for m in d["geometry"]["meshes"] if m["id"] == "collision")
    stg = Usd.Stage.Open(str(p.folder / uri))
    assert UsdGeom.GetStageUpAxis(stg) == "Z" and UsdGeom.GetStageMetersPerUnit(stg) == 1
    mesh = UsdGeom.Mesh(stg.GetPrimAtPath("/collision/mesh"))
    pts, counts, idx = mesh.GetPointsAttr().Get(), mesh.GetFaceVertexCountsAttr().Get(), mesh.GetFaceVertexIndicesAttr().Get()
    assert len(pts) == 164 and set(counts) == {3} and len(idx) == 3 * len(counts) == 3 * 234
    up = 0
    for i in range(0, len(idx), 3):                      # synthetic road faces up: normals have +z
        a, b, c = (pts[k] for k in idx[i:i + 3])
        nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        up += nz > 0
    assert up == len(counts)
    from pxr import UsdGeom as G
    names = {pv.GetPrimvarName() for pv in G.PrimvarsAPI(mesh).GetPrimvars()}
    assert {"stj_surf_layers_new_dry_0", "stj_surf_weights_worn_wet"} <= names


def test_nine_surface_maps_with_wear_and_wetness_conditions(pkg):
    d = doc_of(pkg[0])
    maps = d["surfaces"]["maps"]
    assert len(maps) == 9
    seen = set()
    for m in maps:
        assert m["contact"] == "blend" and m["weights"]["encoding"] == "vertex_top3" and m["material"] == "Ground"
        cond = json.dumps(m["when"])
        seen.add(m["_note"])
        assert '"wear"' in cond and '"wetness"' in cond
    assert len(seen) == 9
    # all states resolvable by the evaluation rule: first map whose condition holds
    ge = [c for c in maps[0]["when"]["all"]]
    assert ge[0] == {"signal": "wear", "op": "lt", "value": 0.25}


def test_surface_library_has_classes_without_grip_numbers(pkg):
    lib = doc_of(pkg[0])["surfaces"]["library"]
    g = lib["rbr_gravel_fine_deep_dry"]
    assert g["class"] == "gravel" and g["x_rbr"] == {"id": 9, "name": "Gravel_Fine_Deep_Dry"} and g["_est"] is True
    assert lib["rbr_grass_medium_wet"]["class"] == "grass"
    assert not any("mf_scaling" in e for e in lib.values())            # no invented grip


def test_blend_weights_and_cell_lookup(stage):
    """Verge corners blend gravel -> grass with attr A (cell 0) and with the .mat cell lookup (cell 15)."""
    st = next(s for s in stage.collision.states if s.key == "normal_dry")
    gravel, grass = 9, 105
    assert st.palette == [gravel, grass]
    w = st.weights
    assert any(wa == 1.0 for wa, _ in w) and any(wb == 1.0 for _, wb in w) and any(wa == 0.0 for wa, _ in w)
    # blend via attr A: weight 31/31 on the outer corner picks surface 1 (grass) fully; layers are (grass, gravel, ..)
    assert any(lay[0] == 1 for lay in st.layers)
    assert all(0 <= i < len(st.palette) for lay in st.layers for i in lay)
    assert all(0 <= wa <= 1 and 0 <= wb <= 1 and wa + wb <= 1.0001 for wa, wb in w)


def test_identical_states_collapse_to_one_unconditional_map(tmp_path):
    build.build_classic_stage(tmp_path / "s", same_mat_entries=True)
    st = convert_classic(tmp_path / "s")
    assert len(st.collision.states) == 1 and len(st.collision.states[0].merged) == 9
    p = package.write_package([st], tmp_path / "out", validate=False)
    maps = doc_of(p)["surfaces"]["maps"]
    assert len(maps) == 1 and "when" not in maps[0]
    assert maps[0]["weights"]["weights"] == "stj_surf_weights"


def test_default_state_option_writes_one_state(tmp_path, classic_stage):
    st = convert_classic(classic_stage.folder, Options(variant="M", surface_states="default"))
    assert [s.key for s in st.collision.states] == ["normal_dry"]


def test_water_bodies(pkg):
    bodies = {b["id"]: b for b in doc_of(pkg[0])["water"]["bodies"]}
    puddle = bodies["puddle_1"]
    assert puddle["kind"] == "puddle" and puddle["when"] == {"signal": "wetness", "op": "ge", "value": 0.5}
    assert puddle["level"] == 1.0 and len(puddle["outline"]) == 4
    ford = bodies["ford_1"]                                           # crosses the centreline
    assert ford["kind"] == "ford" and ford["drivable"] is True and len(ford["path"]) == 2
    assert ford["path"][0][3] == pytest.approx(8.0)                    # width = the short side of the 8 x 12 m quad
    pond = bodies["pond_1"]
    assert pond["kind"] == "pond" and pond["level"] == 0.0 and "when" not in pond


def test_brake_wall_edges(pkg):
    edges = {e["id"]: e for e in doc_of(pkg[0])["edges"] if e["kind"] == "invisible"}
    assert [(e["behaviour"], e["side"]) for e in edges.values()] == [("drag", "left"), ("sensor", "left"), ("respawn", "left")]
    drag = edges["brake_wall_0"]
    assert drag["drag_coefficient"] > 0 and drag["_est"] is True and drag["x_rbr"]["items"] == [0, 1]
    assert drag["t"] == pytest.approx(6.0, abs=0.05) and drag["s_start"] < drag["s_end"]


# ── props and fences (P3) ────────────────────────────────────────────────────

def test_fences_become_barrier_edges(pkg):
    edges = {e["id"]: e for e in doc_of(pkg[0])["edges"] if e["kind"] == "barrier"}
    tape, net = edges["fence_0_0"], edges["fence_1_0"]
    assert (tape["style"], tape["tearable"], tape["side"]) == ("tape", True, "left")
    assert (net["style"], net["tearable"], net["side"]) == ("fence", False, "right")
    assert len(tape["x_rbr"]["posts"]) == 6 and tape["t"] == pytest.approx(5.4, abs=0.1)
    assert net["t"] == pytest.approx(-5.8, abs=0.1)


def test_props_prototypes_and_instances(pkg):
    d = doc_of(pkg[0])
    pr = d["scenery"]["prototypes"]
    assert set(pr) == {"cone", "sign", "ho_bush01"}
    assert pr["cone"]["class"] == "prop" and pr["cone"]["physics"]["mode"] == "movable" and pr["cone"]["physics"]["_est"]
    assert pr["sign"]["class"] == "sign" and pr["sign"]["breakable"] is True
    assert pr["ho_bush01"]["class"] == "bush"
    inst = {i["prototype"]: i for i in d["scenery"]["instances"]}
    assert len(inst["cone"]["transforms"]) == 3 and inst["cone"]["transforms"][0] == [100.0, 5.0, 0.2, 0.0]
    assert inst["sign"]["transforms"][0][3] == pytest.approx(math.pi / 2, abs=1e-3)   # quaternion yaw 90 degrees
    prim = pr["cone"]["mesh"]
    assert prim == {"mesh_ref": "props", "prim": "/props/cone"}


# ── variants, weather, environment (P4) ──────────────────────────────────────

def test_weather_options_only_for_converted_letters(pkg):
    env = doc_of(pkg[0])["environment"]
    secs = [o["x_rbr"]["section"] for o in env["weather_options"]]
    assert "101M_hazy_clear" in secs and "101N_hazy_clear" not in secs and "101O_E_hazy_partcloud" not in secs
    assert env["default_time"]["local_time"] == "09:00"
    cond = {o["x_rbr"]["section"]: o["weather"]["condition"] for o in env["weather_options"]}
    assert cond["101M_lightrain_heavycloud"] == "rain" and cond["101M_heavysnow_heavycloud"] == "snow"


def test_all_variants_make_override_layers(tmp_path, classic_stage):
    first = convert_classic(classic_stage.folder, Options(variant="M"))
    other = convert_classic(classic_stage.folder, Options(variant="N"))
    p = package.write_package([first, other], tmp_path / "o", validate=False)
    d = doc_of(p)
    assert d["variants"] == [{"id": "variant_n", "name": "Night", "kind": "time_of_day", "override": "test_stage_n.usda"}]
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(p.folder / "test_stage_n.usda"))
    o = json.loads(layer.customLayerData["stj"])
    assert o["_metadata"]["specification"] == "STJ-OVERRIDE" and o["_metadata"]["base"] == "./test_stage.usda"
    patch = o["patch"]
    assert patch["environment"]["default_time"]["local_time"] == "22:00"
    assert len(patch["surfaces"]["maps"]) == 6                          # the night .mat has only 6 entries
    assert "_metadata" not in patch and "scenery" not in patch          # props file identical: shared, not repeated
    assert any(o.get("variant") == "variant_n" for o in d["environment"]["weather_options"])


def test_open_question_7_identical_files_share_the_mesh(tmp_path, classic_stage):
    """When a variant's .col and .mat are byte-identical the mesh layer is reused and the patch holds only presentation."""
    first = convert_classic(classic_stage.folder, Options(variant="M"))
    other = convert_classic(classic_stage.folder, Options(variant="M"))
    other.info.letter = "E"
    p = package.write_package([first, other], tmp_path / "o", validate=False)
    assert not (p.folder / "meshes" / "test_stage_e_collision.usda").exists()
    from pxr import Sdf
    patch = json.loads(Sdf.Layer.FindOrOpen(str(p.folder / "test_stage_e.usda")).customLayerData["stj"])["patch"]
    assert set(patch) == {"environment"}


def test_merge_diff_is_rfc7396():
    base = {"a": 1, "b": {"c": 2, "d": [1, 2]}, "e": 5}
    other = {"a": 1, "b": {"c": 3, "d": [1, 2, 3]}, "f": 6}
    assert package.merge_diff(base, other) == {"b": {"c": 3, "d": [1, 2, 3]}, "e": None, "f": 6}
    assert package.merge_diff(base, base) == {}


# ── origin, options, safety ──────────────────────────────────────────────────

def test_far_origin_is_recentred_and_recorded(tmp_path):
    build.build_classic_stage(tmp_path / "far", translate=(20000.0, -15000.0))
    st = convert_classic(tmp_path / "far", Options(no_geometry=True))
    assert st.origin_offset is not None
    cx, cy, _ = st.origin_offset
    assert abs(cx) > 15000 and max(abs(p[0]) for p in st.line.points) < 1000
    d = stj_doc.build_document(st, mesh_files={})
    assert d["environment"]["origin_offset"] == [cx, cy, 0.0]
    assert st.line.points[0][0] + cx == pytest.approx(20000.0)           # x_original = x + origin_offset


def test_no_geometry_option(tmp_path, classic_stage):
    st = convert_classic(classic_stage.folder, Options(variant="M", no_geometry=True))
    p = package.write_package([st], tmp_path / "ng", validate=False)
    d = doc_of(p)
    assert "geometry" not in d and "surfaces" not in d and "edges" not in d and "scenery" not in d
    assert "race" in d and "timing" in d and not (p.folder / "meshes").exists()


def test_encrypted_input_is_refused_by_the_cli(tmp_path, capsys):
    build.build_classic_stage(tmp_path / "enc", with_rsf=False)
    (tmp_path / "enc" / "track-101_M.trk").write_bytes(b"\x01\xa0\x00\x00" + bytes(64))
    rc = cli.main(["classic", str(tmp_path / "enc"), "-o", str(tmp_path / "out")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "encrypted by RallySimFans" in err and "never decrypts" in err
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_source_folder_is_never_written(tmp_path, classic_stage):
    before = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in classic_stage.folder.iterdir())
    cli.main(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o"), "--no-validate"])
    after = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in classic_stage.folder.iterdir())
    assert before == after


def test_user_surface_table_adds_numbers(tmp_path, classic_stage):
    st = convert_classic(classic_stage.folder, Options(variant="M"))
    table = {"9": {"mf_scaling": {"LMUX": 0.8, "LMUY": 0.75}}, "Grass_Medium_Dry": {"rolling_resistance": 0.05}}
    p = package.write_package([st], tmp_path / "o", user_table=table, validate=False)
    lib = doc_of(p)["surfaces"]["library"]
    assert lib["rbr_gravel_fine_deep_dry"]["mf_scaling"] == {"LMUX": 0.8, "LMUY": 0.75}
    assert lib["rbr_grass_medium_dry"]["rolling_resistance"] == 0.05


# ── strict validation with STJ's own validator ───────────────────────────────

stj_tools = package.find_stj_tools()
needs_validator = pytest.mark.skipif(stj_tools is None, reason="STJ tools not found (reference/STJ or STJ_TOOLS)")


@needs_validator
def test_classic_package_passes_strict_validation(pkg):
    code, text = package.run_validate(pkg[0].root, stj_tools)
    assert code == 0, text


@needs_validator
def test_classic_variants_package_passes_strict_validation(tmp_path, classic_stage):
    stages = [convert_classic(classic_stage.folder, Options(variant=v)) for v in ("M", "N")]
    p = package.write_package(stages, tmp_path / "v", validate=False)
    code, text = package.run_validate(p.root, stj_tools)
    assert code == 0, text
    assert "variants[0]" not in text or "PASS" in text


@needs_validator
def test_extracted_json_matches_embedded_document(pkg):
    import subprocess
    import sys
    out = pkg[0].folder / "x.json"
    r = subprocess.run([sys.executable, str(stj_tools / "stj_usd.py"), "extract", str(pkg[0].root), "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == doc_of(pkg[0])


@needs_validator
def test_our_reference_line_matches_stj_core(stage):
    import sys
    sys.path.insert(0, str(stj_tools))
    import stj_core
    theirs = stj_core.ReferenceLine(stage.line.points, tangents_out=stage.line.tangents, tangents_in=stage.line.tangents_in, chainage=stage.line.chainage)
    ours = RefLine(stage.line.points, stage.line.tangents, stage.line.chainage, stage.line.tangents_in)
    for s in (0.0, 123.4, 250.0, 433.3, 700.0):
        a, b = ours.frame(s), theirs.frame(s)
        assert a[:3] == pytest.approx(b[:3], abs=1e-6) and a[3] == pytest.approx(b[3], abs=1e-6)
    assert ours.project(160.0, -3.0) == pytest.approx(theirs.project(160.0, -3.0), abs=0.2)


def test_primary_surface_falls_back_to_the_collision_surfaces(tmp_path):
    """A retail stage has no RSF Tracks<id>.ini: the dominant surface class of the collision mesh says what it is."""
    build.build_classic_stage(tmp_path / "retail", with_rsf=False)
    st = convert_classic(tmp_path / "retail")
    assert st.info.surface == "gravel" and "primary_surface_from_collision" in st.diagnostics
    d = stj_doc.build_document(st, mesh_files={})
    assert d["track_info"]["primary_surface"] == "gravel"


def test_stage_files_inside_an_rbz_archive(tmp_path, classic_stage):
    """Retail stages may ship as `.rbz` (plain ZIP): the files are found inside it, case-insensitively."""
    import zipfile
    d = tmp_path / "maps"
    d.mkdir()
    with zipfile.ZipFile(d / "track-101_M.rbz", "w") as z:
        for ext in ("trk", "dls", "col", "mat", "fnc", "lbs", "ini"):
            z.write(classic_stage.folder / f"track-101_M.{ext}", f"Maps/TRACK-101_M.{ext.upper()}")
    (d / "track-101_M_textures.rbz").write_bytes(b"\x01\xa0\x00\x00not a zip")      # encrypted textures: never opened
    st = convert_classic(d / "track-101_M.rbz")
    assert st.info.letter == "M" and len(st.line.points) == len(classic_stage.points) and st.collision is not None
    st2 = convert_classic(d)
    assert st2.timing.finish == 690.0


def test_encrypted_member_inside_an_rbz_is_refused(tmp_path, classic_stage):
    import zipfile
    d = tmp_path / "maps"
    d.mkdir()
    with zipfile.ZipFile(d / "track-101_M.rbz", "w") as z:
        for ext in ("trk", "dls", "mat"):
            z.write(classic_stage.folder / f"track-101_M.{ext}", f"track-101_M.{ext}")
        z.writestr("track-101_M.col", b"\x01\xa0\x00\x00" + bytes(64))
    with pytest.raises(ProtectedFileError):
        convert_classic(d / "track-101_M.rbz")
