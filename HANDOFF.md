# Handoff: test rbr2stj on a machine with Richard Burns Rally

## Why

`rbr2stj` converts RBR stages (classic and BTB) to STJ v0.3 packages. It was built and tested only on **synthetic**
stages, because the build machine had no RBR install. Four questions can only be answered with real files:

| | Question | How the tool answers it |
|---|---|---|
| Q1 | Are driveline tangents per segment (we assume so) | tangent/chord ratio per stage |
| Q2 | Is the driveline `distance` 3D or x/y length | stored length vs x/y length vs 3D length |
| Q3 | Is the frame right (not mirrored) | left/right notes vs the bend that follows each |
| Q7 | Do the letters M/N/E/O share `.col`/`.mat`/`.trk` | file hashes compared per stage |

It also shows whether the readers survive real files at all (the layouts come from reRBR's notes and were only
round-tripped against test writers), how long conversion takes, and how big packages get.

## What is in the zip

`rbr2stj-handoff.zip` = the project (source, tests, docs, tools, README) plus a copy of STJ's tools and schema in
`reference/STJ/` so the strict validator works offline. No game data, no `.git`, no build output.

## Setup (Windows, PowerShell)

Needs Python 3.12 or newer.

```powershell
Expand-Archive rbr2stj-handoff.zip -DestinationPath rbr2stj
cd rbr2stj
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
python -m pytest -q
```

Expected: `134 passed, 1 skipped`. (The skipped one is the real-data test.) If anything else fails, stop and send the output.

Optional smoke test with synthetic stages: `python tools/make_synthetic.py out/synthetic`, then
`python -m rbr2stj classic "out/synthetic/classic/101-Test Stage" -o out/t --all-variants` (should end with
`validate --strict: exit 0`).

## Run on the real install

Point it at the install folders (they are only read; nothing is written into them):

```powershell
# RSF install (has Maps\<id>-<name>\ and RX_CONTENT\Tracks\) and/or retail RBR (has Maps\track-NN_M.*)
python tools/collect_report.py --rsf "C:\Games\RSF" --rbr "C:\Games\RBR" --validate --all-letters -o report
```

- It converts every stage into a temp folder, prints one line per stage, and writes `report.json` and `report.md`.
- **Encrypted RSF stages are counted as "protected" and skipped.** That is expected (about a third of a full RSF install).
- If it is too slow or memory hungry on huge BTB tracks: add `--limit 20` first, and/or `--surface-states default`.
  Conversion costs roughly 110 microseconds per collision face with all nine surface states.
- To inspect one stage by hand:

```powershell
python -m rbr2stj info "C:\Games\RBR\Maps\track-54_M.col"
python -m rbr2stj classic "C:\Games\RBR\Maps\track-54_M.trk" -o out\tanner
python -m rbr2stj classic "C:\Games\RSF\Maps\101-Autiovaara II" -o out\autiovaara --all-variants
python -m rbr2stj btb "C:\Games\RSF\RX_CONTENT\Tracks\Pirttikulma" -o out\pirttikulma
```

Retail `Maps` holds many stages: pass a `track-NN_L.trk` file (or a `.rbz`) there, not the folder.
Each package is a folder with a `.usda` root layer, an extracted `<name>.stj.json` and `meshes/`.

## What to send back

Send **only** these (they contain no game content: stage folder names, counts, timings, sizes, error messages):

1. `report.md` and `report.json`
2. For any stage with status `error` or `crash`: the line from `report.md` (the traceback is in `report.json`)
3. Anything odd you saw by hand (a package that opens as garbage in a USD viewer, a stage that looks mirrored, ...)

**Do not send stage files, textures or converted packages.** Converted packages are marked `redistributable: false`
and come from content you may use but not share.

## How to read the report

- **Q1**: `Q1_tangent_to_chord.median_of_medians` near 1 (0.5 to 2) confirms "per segment, no scaling". Far from 1 means
  tangents are per metre or another unit: tell me the value.
- **Q2**: `matches_xy_within_1pct` high, `matches_3d_within_1pct` low means distance is x/y. The reverse means 3D, and
  then `s` should be rescaled. If both match, the roads are flat; look at `median_stored_over_xy`.
- **Q3**: `agree` should dominate. `disagree` much larger than `agree` means the stage comes out **mirrored**: that is the
  most important thing to report. A few disagreements are normal (notes placed before a bend, S-bends).
- **Q7**: `col_identical` equal to `stages_with_several_letters` means the letters share geometry.
- `validate_failures` should be empty. Any entry is a bug: send its `validate_text` from `report.json`.
- `errors`/`crashes`: most likely a reader hitting a real-file layout the notes did not cover. The message names the file
  and offset.

## Rules (keep them)

- Only files you may use. RSF-encrypted files (`01 a0 00 00`) are refused and never decoded; do not try to work around it.
- Never copy the converted output or game files into a public place or a repo.
- The project is Apache-2.0; the formats were learned from reRBR's public notes (GPL-3.0) without copying its code.

## Known soft spots (most likely first failures)

- `.col` brake wall: item index to segment is inferred, no real file was available.
- `.col` tree reading assumes the layout in reRBR's notes; a strict-tiling difference would show as a `.col` format error.
- BTB tracks with millions of vertices may be slow or need a lot of RAM (the largest in the install are about 3.4 M vertices).
- Classic `.lbs` visual geometry only decodes vertex slots 0, 5, 6, 7, 8; chunks on other slots are skipped and counted in `lost`.
- Surface classes, road width, ford depth, drag coefficient and prop masses are marked `_est` placeholders.

More detail: `README.md` and `docs/mapping.md` (mapping tables, open questions with evidence, the full `lost` list).
