"""Convert every stage of an RBR / RSF install and write a SMALL report (no game content) to send back.

    python tools/collect_report.py --rsf "C:/Games/RSF" --rbr "C:/Games/RBR" -o report [--all-letters] [--validate]

Walks retail `<RBR>/Maps/track-NN_*.trk`, RSF `<RSF>/Maps/<id>-<name>/` and BTB `<RSF>/RX_CONTENT/Tracks/<folder>/`,
converts each stage into a temporary folder (never into the install), and writes `report.json` and `report.md` with
only: stage names (folder names), timings, counts, sizes, the diagnostics that answer the open questions, validator
results and error messages. Encrypted stages are counted, never decoded. Nothing from the game's files is copied.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rbr2stj.convert import Options  # noqa: E402
from rbr2stj.convert.btb import convert_btb  # noqa: E402
from rbr2stj.convert.classic import convert_classic  # noqa: E402
from rbr2stj.errors import ProtectedFileError, Rbr2StjError  # noqa: E402
from rbr2stj.readers import btb as btb_reader  # noqa: E402
from rbr2stj.readers import stage_files  # noqa: E402
from rbr2stj.writers import package  # noqa: E402


def _work(job):
    """One stage in a worker process: (kind, name, spec, validate, tools) -> record."""
    kind, name, spec, validate, tools = job
    if spec[0] == "classic":
        _t, path, letter, states = spec
        fn = lambda: convert_classic(path, Options(variant=letter, surface_states=states))  # noqa: E731
    else:
        fn = lambda: convert_btb(spec[1], Options())  # noqa: E731
    tmp = Path(tempfile.mkdtemp(prefix="rbr2stj_report_"))
    try:
        return run_one(kind, name, fn, tmp, validate, tools)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def classic_targets(rbr: Path | None, rsf: Path | None, all_letters: bool):
    roots = []
    if rbr and (rbr / "Maps").is_dir():
        roots.append(("retail", rbr / "Maps"))
    if rsf and (rsf / "Maps").is_dir():
        roots += [("rsf", d) for d in sorted((rsf / "Maps").iterdir()) if d.is_dir()]
    for kind, root in roots:
        try:
            stems = stage_files.variants_in(root)
        except OSError:
            continue
        for stem, letters in sorted(stems.items()):
            for L in (letters if all_letters else letters[:1]):
                yield kind, (f"{root.name}/{stem}_{L}" if kind == "rsf" else f"{stem}_{L}"), root / f"{stem}_{L}.trk", L


def btb_targets(rsf: Path | None):
    if rsf and (rsf / "RX_CONTENT" / "Tracks").is_dir():
        for d in sorted((rsf / "RX_CONTENT" / "Tracks").iterdir()):
            if btb_reader.is_btb_folder(d):
                yield d


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def run_one(kind, name, convert, tmp: Path, validate: bool, tools):
    rec = {"kind": kind, "name": name}
    t0 = time.time()
    try:
        st = convert()
        rec["convert_s"] = round(time.time() - t0, 2)
        out = tmp / "pkg"
        shutil.rmtree(out, ignore_errors=True)
        t1 = time.time()
        pkg = package.write_package([st], out, Options(), validate=False)
        rec["write_s"] = round(time.time() - t1, 2)
        rec["package_bytes"] = dir_size(out)
        d = st.diagnostics
        rec["diagnostics"] = {k: d[k] for k in ("chainage_vs_geometry", "tangent_to_chord_ratio", "collision", "pacenotes",
                                                "identical_states", "visual", "objects", "skipped_objects",
                                                "chainage_fallback", "primary_surface_from_collision") if k in d}
        rec["hashes"] = {k: v[:12] for k, v in st.file_hashes.items()}
        rec["lines"] = len(st.line.points)
        rec["notes"] = len(st.notes)
        rec["states"] = len(st.collision.states) if st.collision else 0
        rec["lost_count"] = len(st.lost)
        rec["lost_stage_specific"] = [l for l in st.lost][:20]
        rec["variants_available"] = st.x_rbr.get("variants_available")
        if validate:
            code, text = package.run_validate(pkg.root, tools)
            rec["validate_exit"] = code
            if code not in (0, -1):
                rec["validate_text"] = text[-1500:]
        rec["status"] = "ok"
    except ProtectedFileError:
        rec["status"] = "protected"
    except Rbr2StjError as e:
        rec["status"] = "error"
        rec["error"] = str(e)[:400]
    except Exception as e:  # noqa: BLE001 - a report must survive any single stage
        rec["status"] = "crash"
        rec["error"] = f"{type(e).__name__}: {e}"[:400]
        rec["traceback"] = traceback.format_exc()[-1200:]
    return rec


def summarize(recs):
    ok = [r for r in recs if r["status"] == "ok"]
    s = {"stages": len(recs), "ok": len(ok), "protected": sum(r["status"] == "protected" for r in recs),
         "errors": sum(r["status"] == "error" for r in recs), "crashes": sum(r["status"] == "crash" for r in recs)}
    cg = [r["diagnostics"]["chainage_vs_geometry"] for r in ok if "chainage_vs_geometry" in r["diagnostics"]]
    if cg:
        def rel(a, b):
            return abs(a - b) / b if b else 0
        s["Q2_chainage"] = {
            "stages": len(cg),
            "matches_xy_within_1pct": sum(rel(c["stored_length"], c["xy_length"]) <= 0.01 for c in cg),
            "matches_3d_within_1pct": sum(rel(c["stored_length"], c["length_3d"]) <= 0.01 for c in cg),
            "matches_neither": sum(rel(c["stored_length"], c["xy_length"]) > 0.01 and rel(c["stored_length"], c["length_3d"]) > 0.01
                                   for c in cg),
            "median_stored_over_xy": round(statistics.median(c["stored_length"] / c["xy_length"] for c in cg if c["xy_length"]), 5)}
    tr = [r["diagnostics"]["tangent_to_chord_ratio"] for r in ok if "tangent_to_chord_ratio" in r["diagnostics"]]
    if tr:
        s["Q1_tangent_to_chord"] = {"median_of_medians": round(statistics.median(t["median"] for t in tr), 3),
                                    "min": min(t["min"] for t in tr), "max": max(t["max"] for t in tr)}
    pn = [r["diagnostics"]["pacenotes"]["direction_vs_geometry"] for r in ok if "pacenotes" in r["diagnostics"]]
    if pn:
        s["Q3_handedness"] = {k: sum(p[k] for p in pn) for k in ("agree", "disagree", "unclear")}
    groups: dict[str, list] = {}
    for r in ok:
        if r["kind"] != "btb" and r.get("hashes"):
            groups.setdefault(r["name"].rsplit("_", 1)[0], []).append(r["hashes"])
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if multi:
        same = lambda ext: sum(len({h.get(ext) for h in v}) == 1 for v in multi.values())  # noqa: E731
        s["Q7_variants_share_files"] = {"stages_with_several_letters": len(multi), "col_identical": same("col"),
                                        "mat_identical": same("mat"), "trk_identical": same("trk"),
                                        "lbs_identical": same("lbs")}
    s["validate_failures"] = [r["name"] for r in ok if r.get("validate_exit") not in (None, 0, -1)]
    s["convert_s_total"] = round(sum(r.get("convert_s", 0) for r in ok), 1)
    s["largest_package_mb"] = round(max((r.get("package_bytes", 0) for r in ok), default=0) / 1e6, 1)
    return s


def markdown(summary, recs):
    L = ["# rbr2stj real-data report", "", "```json", json.dumps(summary, indent=2), "```", ""]
    bad = [r for r in recs if r["status"] in ("error", "crash")]
    L.append(f"## Failures ({len(bad)})")
    for r in bad:
        L.append(f"- `{r['name']}` [{r['status']}] {r.get('error', '')}")
    L += ["", "## Slowest / largest"]
    ok = [r for r in recs if r["status"] == "ok"]
    for r in sorted(ok, key=lambda r: -r.get("convert_s", 0))[:5]:
        L.append(f"- `{r['name']}`: convert {r['convert_s']} s, write {r['write_s']} s, {r['package_bytes'] / 1e6:.1f} MB, "
                 f"{r.get('diagnostics', {}).get('collision', {}).get('triangles', 0)} collision triangles")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rbr", type=Path, help="retail RBR install (has Maps/)")
    ap.add_argument("--rsf", type=Path, help="RSF install (has Maps/<id>-<name>/ and RX_CONTENT/Tracks/)")
    ap.add_argument("-o", "--out", type=Path, default=Path("report"), help="output prefix: <prefix>.json and <prefix>.md")
    ap.add_argument("--all-letters", action="store_true", help="convert every variant letter, not just the first")
    ap.add_argument("--validate", action="store_true", help="run STJ validate.py --strict on every package")
    ap.add_argument("--surface-states", choices=["all", "default"], default="all", help="default = faster, one surface map")
    ap.add_argument("--jobs", type=int, default=1, help="stages converted at the same time (processes)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N stages (0 = all)")
    a = ap.parse_args(argv)
    if not (a.rbr or a.rsf):
        ap.error("give --rbr and/or --rsf")
    tools = package.find_stj_tools()
    if a.validate and tools is None:
        print("STJ tools not found: validation skipped (set STJ_TOOLS or use reference/STJ)")
    jobs = [(k, n, ("classic", p, L, a.surface_states), a.validate, tools) for k, n, p, L in
            classic_targets(a.rbr, a.rsf, a.all_letters)]
    jobs += [("btb", d.name, ("btb", d), a.validate, tools) for d in btb_targets(a.rsf)]
    if a.limit:
        jobs = jobs[:a.limit]
    recs = []
    if a.jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for i, rec in enumerate(ex.map(_work, jobs, chunksize=1), 1):
                recs.append(rec)
                print(f"[{i}/{len(jobs)}] {rec['status']:9s} {rec['kind']:6s} {rec['name']}", flush=True)
    else:
        for i, job in enumerate(jobs, 1):
            rec = _work(job)
            recs.append(rec)
            print(f"[{i}/{len(jobs)}] {rec['status']:9s} {rec['kind']:6s} {rec['name']}", flush=True)
    summary = summarize(recs)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{a.out}.json").write_text(json.dumps({"summary": summary, "stages": recs}, indent=1), encoding="utf-8")
    Path(f"{a.out}.md").write_text(markdown(summary, recs), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {a.out}.json and {a.out}.md: send these two files back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
