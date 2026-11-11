# Handoff for the next chat: rbr2stj

Read this first, then `README.md`, `docs/mapping.md` and `PLAN.md`. (`HANDOFF.md` is the separate checklist for running
the tool on a machine with Richard Burns Rally.)

## What this is

`rbr2stj` (Python, GPL-3.0-or-later) converts Richard Burns Rally stages, classic (`track-NN_<L>.*`) and BTB
(`RX_CONTENT/Tracks/<folder>`), to **STJ v0.3** packages (https://github.com/RFloEng/STJ-Standard-Track-Json): a root
`.usda` with the STJ document in `customLayerData["stj"]`, USD mesh layers, variant override layers, `<name>.stj.json`.
Formats were learned from reRBR (https://github.com/JodusNodus/rerbr, GPL-3.0) notes and code; **no code is copied**.
The repo will be published under the **RFloEng** GitHub account.

## State (all phases P0 to P7 done)

- `python -m pytest` : 134 passed, 1 skipped (`tests/test_real.py`, needs `RBR_DATA` / `RBR_RSF`).
- Synthetic stages convert and pass STJ `validate.py --strict` (0 errors, 0 warnings): `python tools/make_synthetic.py out/synthetic`
  then `python -m rbr2stj classic "out/synthetic/classic/101-Test Stage" -o out/x --all-variants`, `python -m rbr2stj btb "out/synthetic/btb/Synthetic BTB" -o out/y`.
- Git: one local commit (`Add rbr2stj: ...`), author `RFloEng <RFloEng@users.noreply.github.com>`, no remote, nothing pushed.
  Uncommitted: `.gitignore` (adds `dist/`, `*.zip`), `tools/collect_report.py`, `HANDOFF.md`, this file.
- Tested on **synthetic data only**: no RBR install was available. Real files are the next big step.

## Next steps (in order)

1. **Run on real stages** on a machine with RBR/RSF: `python tools/collect_report.py --rsf <RSF> --rbr <RBR> --validate --all-letters -o report`
   (see `HANDOFF.md`). The report answers Q1 (tangents), Q2 (distance 3D vs x/y), Q3 (mirrored or not), Q7 (variants share files).
   Fix whatever real files break (most likely `.col` tree reading, brake wall, big BTB tracks).
2. **Publication checklist** (decided, not yet done): add copyright holder + `NOTICE` + SPDX headers (holder name is the user's call),
   README non-affiliation notice ("unofficial, not affiliated with RBR rights holders or RallySimFans"), `.gitattributes`
   (`* text=auto eol=lf`), drop or trim `PLAN.md` from the public repo, double-check `col.py` / `dls.py` / `edges.py` vs reRBR for
   anything too close to its Rust (fence table in `edges.py` and surface names are data copied as facts, cited).
3. Optional: speed up the nine-state surface computation (about 110 microseconds per face), a CLI `--no-visual`, a surface-table template.

## Hard rules (keep)

- Never decrypt or decode protected files: starts `01 a0 00 00` / `01 a1 00 00` (RSF) or `21 19 19 19` (obscured `.lbs`): refuse with a clear message.
  Leave XOR `.lsp` alone. Output is `licence.redistributable: false` with a full `provenance.converted_from[].lost`.
- Every binary reader has a test-only writer twin in `tests/fixtures/build.py`; real-data tests run only with `RBR_DATA`/`RBR_RSF`.
- Never write into the game folder. Textures/assets are referenced by name, never embedded.
- Do not copy or translate reRBR code. Cite the note a reader follows.
- **User preference:** their professional email must not appear anywhere (files or git history). Use the noreply identity
  above (they may supply the real GitHub noreply address). Do not commit or push unasked.

## Map of the code

```
src/rbr2stj/  cli.py binio.py guard.py ini.py ir.py info.py errors.py
  readers/    chunks trk dls col mat fnc lbs stage_files rsf btb xfile xform surfaces_table
  convert/    classic btb pacenotes project surfaces edges
  writers/    stj_doc usd_mesh lbs_visual package
tests/        fixtures/build.py (writers + synthetic stage builders), test_readers/convert_classic/convert_btb/cli/pacenotes/lbs_visual/real
tools/        extract_surface_ids.py, make_synthetic.py, collect_report.py
docs/mapping.md   tables, open questions answered, lost list
```

Key design: readers -> IR (`ir.py`, already in the STJ frame, chainage shifted to 0) -> `writers/stj_doc.py` (document) +
`usd_mesh.py` (collision mesh with `stj_surf_*` primvars, props, BTB visual instancers) + `lbs_visual.py` -> `package.py`
(root layer, variants as override layers using RFC 7396 diff, optional `.usdz`, runs STJ validator when found).

## Decisions worth knowing

- Tangents are already per segment dP/du (game's Hermite uses them raw): written unscaled.
- Nine wear x wetness surface maps in one mesh with per-state primvars, `when` on the `wear`/`wetness` signals; identical states merge.
- `Tracks<id>.ini` `Surface`: 0 tarmac, 1 gravel, 2 snow (PLAN.md had it inverted).
- Pace-note tags 29/30/31 = keep right/left/middle; 25 flat right, 26 flat left; 13 distance call skipped.
- Fords are path bodies (validator wants that); brake-wall drag coefficient 0.5 is an `_est` placeholder.
- Estimates all carry `_est`: road width 3.5 m, ford depth 0.15 m, prop masses, default water depth, time of day per variant letter.

## Environment pitfalls

- Windows, Python 3.14, `usd-core` 26.11, `jsonschema`, `pytest`. `rbr2stj.exe` may not be on PATH: use `python -m rbr2stj`.
- STJ tools are found via `STJ_TOOLS`, `reference/STJ/tools`, or a sibling `STJ-Standard-Track-Json/tools`. This zip ships a copy in
  `reference/STJ/` (Apache-2.0). To get the reference material again:
  `git clone --depth 1 https://github.com/JodusNodus/rerbr reference/rerbr` and
  `git clone --depth 1 https://github.com/RFloEng/STJ-Standard-Track-Json reference/STJ` (`reference/` is gitignored; reRBR is GPL, never ship it).
- The Bash tool breaks on some heredocs with nested quotes and Python `write_text` turns LF into CRLF on Windows:
  edit source with the Write/Edit tools, and write bytes (`write_bytes`) when patching from scripts.
- A shared notes folder (`...\side-session-notes\local_fce05a32-...\rbr2stj-build-handover.md`) has a shorter note; it is outside the repo.
