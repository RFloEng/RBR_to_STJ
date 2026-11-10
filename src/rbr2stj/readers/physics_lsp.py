"""The surface and tyre-type tables of an RBR / RSF install's `Physics/` folder (plain text `.lsp` files).

  Physics/physics.lsp  `MaterialManager ("surface types" ...)`: one block per surface id,
      `<Name> ("<Name>" ; id N, rev R  key value ... )` with myStatic, WaterFactor, BumpFrequency, BumpAmplitude,
      CollMyHigh/Low, eHigh/eLow, Type (a grip group: 0 other, 1 gravel, 2 tarmac, 3 snow, 4 dirt, 5 grass), Flags ...
  Physics/<maker>/tyres.lsp  `TyreType00..06 ("(tarmac_dry)" ... SFric_T0..T5 WearFtr_T0..T5 WetSlope_T0..T5 ...)`:
      the tyre's friction on each surface group.

Key names: reRBR data/physics-lsp-fields.md (read, not copied). Both files are readable text in the installs we know;
a file that is not (obfuscated) yields nothing and the converter says so. Nothing is written back.
"""

from __future__ import annotations

import re
from pathlib import Path

GROUPS = {0: "other", 1: "gravel", 2: "tarmac", 3: "snow", 4: "dirt", 5: "grass"}
_SURFACE = re.compile(r'\n(\w+)\s+\("([^"]*)"\s*;\s*id\s+(\d+)[^\n]*\n(.*?)\n\)', re.S)
_KV = re.compile(r'^\s*(\w+)\s+([^\s;]+)', re.M)
_TYRE = re.compile(r'\n\s*TyreType(\d+)\s+\("\(?([^")]*)\)?"(.*?)(?=\n\s*TyreType\d+\s+\(|\Z)', re.S)


def _num(v: str):
    try:
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
    except ValueError:
        return v


def parse_surfaces(text: str) -> dict[int, dict]:
    out = {}
    for name, _n, sid, body in _SURFACE.findall(text):
        row = {k: _num(v) for k, v in _KV.findall(body) if k not in ("Flags",)}
        flags = re.search(r"Flags\s+([^\n]*)", body)
        row["Flags"] = flags.group(1).split() if flags else []
        row["name"] = name
        out[int(sid)] = row
    return out


def parse_tyres(text: str) -> list[dict]:
    out = []
    for idx, name, body in _TYRE.findall(text):
        row = {"index": int(idx), "name": name}
        for key in ("SFric", "WearFtr", "WetSlope"):
            vals = [_num(v) for k, v in _KV.findall(body) if re.fullmatch(rf"{key}_T\d", k)]
            if vals:
                row[key] = vals
        for key in ("WetnessDesign", "WetnessWearFtr"):
            for k, v in _KV.findall(body):
                if k == key:
                    row[key] = _num(v)
        out.append(row)
    return out


def load(physics_dir: Path) -> dict | None:
    """{"source", "surfaces": {id: row}, "tyres": [row]} from a `Physics` folder, or None when it has no readable tables."""
    f = Path(physics_dir) / "physics.lsp"
    if not f.is_file():
        return None
    surfaces = parse_surfaces(f.read_text(encoding="utf-8", errors="replace"))
    if not surfaces:
        return None
    tyres = []
    for t in sorted(Path(physics_dir).glob("*/tyres.lsp")):
        tyres = parse_tyres(t.read_text(encoding="utf-8", errors="replace"))
        if tyres:
            break
    return {"source": f"{Path(physics_dir).name}/physics.lsp" + (f", {t.parent.name}/tyres.lsp" if tyres else ""),
            "surfaces": surfaces, "tyres": tyres}


def find_dir(stage_folder: Path, explicit: Path | None = None) -> Path | None:
    """`Physics` beside the install's `Maps` (classic) or two levels above `RX_CONTENT/Tracks/<name>` (BTB)."""
    if explicit:
        return Path(explicit)
    p = Path(stage_folder).resolve()
    for up in (p.parent.parent, p.parent.parent.parent):
        if (up / "Physics" / "physics.lsp").is_file():
            return up / "Physics"
    return None
