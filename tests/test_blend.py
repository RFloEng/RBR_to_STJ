from pathlib import Path

from rbr2stj.writers import blend


def test_skipped_without_blender(monkeypatch, tmp_path):
    monkeypatch.setattr(blend, "find_blender", lambda: None)
    code, msg, path = blend.write_blend(tmp_path / "x.usda", tmp_path)
    assert code == -1 and path is None and "Blender" in msg


def test_skipped_without_stj_tools(monkeypatch, tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_text("")
    code, msg, path = blend.write_blend(tmp_path / "x.usda", None, blender=exe)
    assert code == -1 and path is None and "tools" in msg


def test_driver_is_valid_python_and_imports_without_reset():
    src = blend._DRIVER.format(tools="t", root="r.usda", out="o.blend", status="s.txt")
    compile(src, "driver", "exec")
    assert "reset=False" in src  # the factory reset would replace the window under the USD import
