"""Write a Blender .blend of a finished package, following the STJ Blender profile.

The decisions (which object, collection and property each part of the document becomes) are the STJ tools':
`tools/blender/stj_blender.py` builds the profile scene from the document that the package's root layer carries.
This module only runs that importer inside Blender and saves the file next to the package root.

By default Blender opens with its window, runs the import, saves and closes itself (the user's rule: Blender is
never run headless unless asked). `background=True` adds `-b` for unattended batches.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_DRIVER = '''import sys, traceback
import bpy
sys.path.insert(0, {tools!r})

def run():
    code = 1
    try:
        import stj_blender
        # reset=False empties the open file: the factory reset would swap the window under the import operator
        def build():
            f = stj_blender.load_into_scene({root!r}, reset=False)
            bpy.ops.wm.save_as_mainfile(filepath={out!r})
            print("wrote", {out!r})
            return 1 if any(x[0] == "ERROR" for x in f) else 0
        win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
        if win is not None:
            with bpy.context.temp_override(window=win, screen=win.screen, area=win.screen.areas[0]):
                code = build()
        else:
            code = build()
    except Exception:
        traceback.print_exc()
    sys.stdout.flush()
    open({status!r}, "w").write(str(code))
    bpy.ops.wm.quit_blender()
    return None

# the window must be up before the USD import operator has a context: run from a timer, not at start
if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=1.0)
'''


def find_blender() -> Path | None:
    """BLENDER_EXE, then PATH, then the usual install folders (newest version first)."""
    env = os.environ.get("BLENDER_EXE")
    if env and Path(env).is_file():
        return Path(env)
    from shutil import which
    w = which("blender")
    if w:
        return Path(w)
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
        for d in sorted(Path(base, "Blender Foundation").glob("Blender *"), reverse=True):
            if (d / "blender.exe").is_file():
                return d / "blender.exe"
    return None


def write_blend(root: Path, tools: Path | None, background: bool = False, blender: Path | None = None,
                timeout: int = 1800) -> tuple[int, str, Path | None]:
    """Returns (exit code, message, .blend path). Code -1 means skipped (Blender or STJ tools missing)."""
    exe = blender or find_blender()
    if exe is None:
        return -1, "Blender not found (set BLENDER_EXE); .blend skipped", None
    bdir = (tools / "blender") if tools else None
    if bdir is None or not (bdir / "stj_blender.py").is_file():
        return -1, "STJ tools/blender not found (set STJ_TOOLS); .blend skipped", None
    out = root.with_suffix(".blend")
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as td:
        status = Path(td) / "status.txt"
        drv = Path(td) / "driver.py"
        drv.write_text(_DRIVER.format(tools=str(bdir), root=str(root), out=str(out), status=str(status)),
                       encoding="utf-8")
        cmd = [str(exe)] + (["-b"] if background else []) + ["--factory-startup", "--python", str(drv)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 1, f"Blender timed out after {timeout}s", None
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        code = int(status.read_text()) if status.exists() and status.read_text().strip().isdigit() else 1
    keep = [ln for ln in text.splitlines() if ln.startswith(("ERROR", "WARN", "NOTE", "ground", "wrote", "Traceback"))
            or "Error" in ln]
    msg = "\n".join(keep) or text[-2000:]
    if not out.exists():
        return 1, msg or "Blender produced no file", None
    return code, msg, out
