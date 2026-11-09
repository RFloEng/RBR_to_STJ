"""rbr2stj command line.

  rbr2stj classic <track-NN_L.trk | stage folder> [--variant M] [--all-variants] -o out/<name>/
  rbr2stj btb     <RX_CONTENT/Tracks/<folder>> -o out/<name>/
  rbr2stj info    <file or folder>

Common options: --surface-table map.json, --no-geometry, --validate / --no-validate, --usdz.
rbr2stj reads only unprotected files and never writes into the game folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .convert import Options
from .errors import Rbr2StjError


def _common(p: argparse.ArgumentParser):
    p.add_argument("-o", "--out", required=True, help="output folder (created; never inside the game folder)")
    p.add_argument("--surface-table", help="JSON file: surface id or name -> STJ surface fields (mf_scaling, mu, ...)")
    p.add_argument("--no-textures", dest="textures", action="store_false",
                   help="do not copy the stage textures (materials then carry the texture name only)")
    p.add_argument("--physics", help="a Physics folder (physics.lsp and */tyres.lsp) for grip numbers; default: the install's own")
    p.add_argument("--no-geometry", action="store_true", help="skip collision, prop and visual meshes")
    p.add_argument("--mesh-format", choices=["auto", "usda", "usdc"], default="auto",
                   help="mesh layer format (auto: usdc above 50,000 faces)")
    p.add_argument("--usdz", action="store_true", help="also write a single-file .usdz (needs the STJ tools)")
    p.add_argument("--blend", action="store_true",
                   help="also write a Blender .blend following the STJ Blender profile (opens Blender 5.2 with its window, "
                        "saves and closes; needs the STJ tools; BLENDER_EXE overrides the location)")
    p.add_argument("--blend-background", action="store_true", help="with --blend: run Blender with -b (no window)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--validate", dest="validate", action="store_true", default=True,
                   help="run the STJ validator with --strict afterwards (default; skipped if the STJ tools are missing)")
    g.add_argument("--no-validate", dest="validate", action="store_false")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rbr2stj", description="Convert Richard Burns Rally stages to STJ packages.")
    ap.add_argument("--version", action="version", version=f"rbr2stj {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classic", help="a classic stage (track-NN_<L>.* files)")
    c.add_argument("path", help="a track-NN_L.trk file or a folder holding one stage")
    c.add_argument("--variant", help="track letter to convert (M, N, E, O or S); default O, M, N, E, S order")
    c.add_argument("--all-variants", action="store_true", help="convert every letter; extras become override layers")
    c.add_argument("--surface-states", choices=["all", "default"], default="all",
                   help="all: nine wear x wetness surface maps; default: only normal wear at the stage's default wetness")
    _common(c)
    b = sub.add_parser("btb", help="a BTB stage folder (RX_CONTENT/Tracks/<folder>)")
    b.add_argument("path")
    _common(b)
    i = sub.add_parser("info", help="summarise a stage file or folder")
    i.add_argument("path")
    return ap


def _table(path):
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Rbr2StjError(f"--surface-table {path}: {e}") from None


def _report(pkg, stages, validate: bool):
    st = stages[0]
    print(f"wrote {pkg.root}")
    for f in pkg.files:
        if f != pkg.root:
            print(f"  {f.relative_to(pkg.folder)}")
    if pkg.usdz:
        print(f"  {pkg.usdz.name}")
    d = st.diagnostics
    if "pacenotes" in d:
        pn = d["pacenotes"]
        print(f"notes: {pn['count']}; left/right vs geometry {pn['direction_vs_geometry']}")
    if getattr(pkg, "blend", None):
        code, text, path = pkg.blend
        print(f"blend: {path.name if path else 'not written'} (exit {code})" if code != -1 else text)
        if code > 0 and text:
            print(text)
    if pkg.validation:
        code, text = pkg.validation
        if code == -1:
            print(text)
        else:
            tail = text.splitlines()[-1] if text else ""
            print(f"validate --strict: exit {code} ({tail})")
            if code:
                print(text)
    bad = bool(pkg.validation and pkg.validation[0] not in (0, -1)) or bool(getattr(pkg, "blend", None) and pkg.blend[0] > 0)
    return 1 if bad else 0


def run_classic(a) -> int:
    from .convert.classic import convert_classic
    from .readers import stage_files
    from .writers.package import write_package
    opt = Options(variant=a.variant, all_variants=a.all_variants, no_geometry=a.no_geometry,
                  surface_states=a.surface_states, mesh_format=a.mesh_format, textures=a.textures, physics=a.physics)
    first = convert_classic(a.path, opt)
    stages = [first]
    if a.all_variants:
        for L in first.x_rbr["variants_available"]:
            if L != first.info.letter:
                stages.append(convert_classic(a.path, Options(**{**opt.__dict__, "variant": L, "all_variants": False})))
    pkg = write_package(stages, Path(a.out), opt, user_table=_table(a.surface_table), usdz=a.usdz, validate=a.validate,
                        blend=a.blend, blend_background=a.blend_background)
    return _report(pkg, stages, a.validate)


def run_btb(a) -> int:
    from .convert.btb import convert_btb
    from .writers.package import write_package
    opt = Options(no_geometry=a.no_geometry, mesh_format=a.mesh_format, textures=a.textures, physics=a.physics)
    st = convert_btb(a.path, opt)
    pkg = write_package([st], Path(a.out), opt, user_table=_table(a.surface_table), usdz=a.usdz, validate=a.validate,
                        blend=a.blend, blend_background=a.blend_background)
    return _report(pkg, [st], a.validate)


def run_info(a) -> int:
    from .info import describe
    print(describe(Path(a.path)))
    return 0


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return {"classic": run_classic, "btb": run_btb, "info": run_info}[a.cmd](a)
    except Rbr2StjError as e:
        print(f"rbr2stj: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
