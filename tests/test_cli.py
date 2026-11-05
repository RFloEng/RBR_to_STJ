"""Command line: classic / btb / info, exit codes and messages."""
import json

import pytest

from rbr2stj import cli
from tests.fixtures import build


def run(args, capsys):
    rc = cli.main(args)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_classic_cli_writes_a_package_and_validates(classic_stage, tmp_path, capsys):
    rc, out, err = run(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o"), "--variant", "M"], capsys)
    assert rc == 0, out + err
    assert "wrote" in out and "test_stage.usda" in out and "validate --strict: exit 0" in out
    for rel in ("test_stage.usda", "test_stage.stj.json", "meshes/test_stage_collision.usda", "meshes/test_stage_props.usda"):
        assert (tmp_path / "o" / rel).is_file(), rel
    doc = json.loads((tmp_path / "o" / "test_stage.stj.json").read_text(encoding="utf-8"))
    assert doc["_metadata"]["licence"]["redistributable"] is False


def test_classic_cli_accepts_a_trk_file_and_all_variants(classic_stage, tmp_path, capsys):
    rc, out, err = run(["classic", str(classic_stage.folder / "track-101_M.trk"), "-o", str(tmp_path / "o"), "--all-variants",
                        "--surface-states", "default"], capsys)
    assert rc == 0, out + err
    assert (tmp_path / "o" / "test_stage_n.usda").is_file()


def test_classic_cli_usdz(classic_stage, tmp_path, capsys):
    rc, out, err = run(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o"), "--variant", "M", "--usdz",
                        "--no-validate"], capsys)
    assert rc == 0, out + err
    z = tmp_path / "o" / "test_stage.usdz"
    assert z.is_file() and "test_stage.usdz" in out
    import zipfile
    assert "test_stage.usda" in zipfile.ZipFile(z).namelist()


def test_errors_have_clear_messages(tmp_path, capsys):
    rc, out, err = run(["classic", str(tmp_path / "nope"), "-o", str(tmp_path / "o")], capsys)
    assert rc == 2 and "no such file or folder" in err
    (tmp_path / "empty").mkdir()
    rc, out, err = run(["classic", str(tmp_path / "empty"), "-o", str(tmp_path / "o")], capsys)
    assert rc == 2 and "no track-<id>_<L>.trk" in err
    rc, out, err = run(["btb", str(tmp_path / "empty"), "-o", str(tmp_path / "o")], capsys)
    assert rc == 2 and "not a BTB stage folder" in err
    (tmp_path / "junk.trk").write_bytes(b"x")
    rc, out, err = run(["classic", str(tmp_path / "junk.trk"), "-o", str(tmp_path / "o")], capsys)
    assert rc == 2 and "not a stage file name" in err


def test_wrong_variant_is_reported(classic_stage, tmp_path, capsys):
    rc, out, err = run(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o"), "--variant", "S"], capsys)
    assert rc == 2 and "variant S not found" in err and "M, N" in err


def test_corrupt_stage_file_is_a_clean_error(tmp_path, capsys):
    build.build_classic_stage(tmp_path / "s", with_rsf=False)
    data = (tmp_path / "s" / "track-101_M.col").read_bytes()
    (tmp_path / "s" / "track-101_M.col").write_bytes(data[:100])
    rc, out, err = run(["classic", str(tmp_path / "s"), "-o", str(tmp_path / "o")], capsys)
    assert rc == 2 or rc == 1
    assert "Traceback" not in err


def test_surface_table_option(classic_stage, tmp_path, capsys):
    table = tmp_path / "t.json"
    table.write_text(json.dumps({"Gravel_Fine_Deep_Dry": {"rolling_resistance": 0.02}}), encoding="utf-8")
    rc, out, err = run(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o"), "--variant", "M", "--surface-table",
                        str(table), "--no-validate"], capsys)
    assert rc == 0
    doc = json.loads((tmp_path / "o" / "test_stage.stj.json").read_text(encoding="utf-8"))
    assert doc["surfaces"]["library"]["rbr_gravel_fine_deep_dry"]["rolling_resistance"] == 0.02
    rc, out, err = run(["classic", str(classic_stage.folder), "-o", str(tmp_path / "o2"), "--surface-table",
                        str(tmp_path / "missing.json")], capsys)
    assert rc == 2 and "--surface-table" in err


@pytest.mark.parametrize("ext,needle", [
    ("trk", "driveline:"), ("dls", "pace-note keys"), ("col", "collision:"), ("mat", "surface entries"),
    ("fnc", "fence groups"), ("lbs", "animation objects"),
])
def test_info_for_each_file_type(classic_stage, capsys, ext, needle):
    rc, out, err = run(["info", str(classic_stage.folder / f"track-101_M.{ext}")], capsys)
    assert rc == 0 and needle in out, out + err


def test_info_on_folder_and_x_files(classic_stage, btb_stage, tmp_path, capsys):
    rc, out, _ = run(["info", str(classic_stage.folder)], capsys)
    assert rc == 0 and "classic stage track-101: variants M, N" in out
    import zipfile
    with zipfile.ZipFile(btb_stage / "Objects.zip") as z:
        (tmp_path / "ground.x").write_bytes(z.read("Objects/ground.x"))
        (tmp_path / "ground.xform").write_bytes(z.read("Objects/ground.xform"))
    rc, out, _ = run(["info", str(tmp_path / "ground.x")], capsys)
    assert rc == 0 and "4 vertices, 2 triangles" in out
    rc, out, _ = run(["info", str(tmp_path / "ground.xform")], capsys)
    assert rc == 0 and "3 placement matrices" in out


def test_info_refuses_protected_and_unknown(tmp_path, capsys):
    (tmp_path / "x.col").write_bytes(b"\x01\xa0\x00\x00....")
    rc, out, err = run(["info", str(tmp_path / "x.col")], capsys)
    assert rc == 2 and "encrypted by RallySimFans" in err
    (tmp_path / "x.zip").write_bytes(b"PK")
    rc, out, err = run(["info", str(tmp_path / "x.zip")], capsys)
    assert rc == 2 and "not a supported file type" in err


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and "rbr2stj" in capsys.readouterr().out
