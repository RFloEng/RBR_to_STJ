# rbr2stj

Convert **Richard Burns Rally** stages to **[STJ — Standard Track JSON](https://github.com/RFloEng/STJ-Standard-Track-Json)**
v0.3 packages: classic stages (`track-NN_<L>.*`, retail and RallySimFans) and BTB stages
(`RX_CONTENT/Tracks/<folder>/`).

The output is a folder package. Its root layer is an OpenUSD `.usda` whose `customLayerData["stj"]` holds the STJ
document (spec §7.8), next to USD mesh layers, variant override layers and an extracted `<name>.stj.json` for diffing.

```
out/<name>/
  <name>.usda                  root layer + embedded STJ document
  <name>.stj.json              the same document as plain JSON
  meshes/<name>_collision.*    ground collision mesh with surface-map primvars (stj_surf_*)
  meshes/<name>_props.usda     collision shapes of cones, signs, bushes ... (classic) and box colliders (BTB)
  meshes/<name>_visual.*       visual geometry: BTB .x meshes (instanced) / classic .lbs render chunks
  <name>_<l>.usda              one override layer per extra variant letter (--all-variants)
```

Textures are copied **unchanged** (original `.dds`/`.tga` bytes, no re-encoding or baking) into the package's `textures/` folder (`--no-textures` to skip). Everything converted from game files is marked
`licence.redistributable: false`, and `_metadata.provenance.converted_from[].lost` lists what was dropped.

## What it reads

| Input | Becomes |
|---|---|
| `.trk` driveline (Hermite points, tangents, stored distance) | `reference.centerline` with `chainage` and `tangents` |
| `.dls` `Driveline` keys | `timing` (start, splits, finish, stop) and `race.pacenotes`; animation triggers in `x_rbr` |
| `.col` + `.mat` | ground collision mesh plus up to nine `surfaces.maps` (wear x wetness, with `when` conditions), blending two surfaces per contact; puddles (`when` wet) and ponds/fords in `water`; the brake wall as invisible `edges` |
| `.trk` meshes, `.fnc` | scenery prototypes/instances (cones, signs, bushes); fences as `edges` (`tape`/`fence`, `tearable`) |
| `.lbs` | start pose, animation object names, visual geometry |
| RSF `Tracks<id>.ini`, `TrackSettings<id>.ini` | `track_info`, `environment.weather_options`, default wetness |
| BTB `driveline.ini`, `pacenotes.ini`, `mat.ini`, `materials.ini`, `objectlist.ini`, `Objects.zip` | line, timing and notes, surfaces from UV cells, collision, box colliders, instanced visual meshes |

Mapping tables, the frame conventions, answers to the format questions and the full `lost` list are in
[docs/mapping.md](docs/mapping.md).

## Install

Python 3.12+ (developed on 3.14). Dependencies: `usd-core`, `jsonschema`.

```bash
pip install -e .
```

This installs the `rbr2stj` command. If your Python `Scripts` folder is not on `PATH`, run it as `python -m rbr2stj`
instead; every example below works either way.

For the optional strict validation you need a checkout of the STJ repository (the converter finds it through the
`STJ_TOOLS` environment variable, `reference/STJ/tools`, or a sibling `STJ-Standard-Track-Json/tools`):

```bash
git clone --depth 1 https://github.com/RFloEng/STJ-Standard-Track-Json reference/STJ
```

## Use

```bash
# a classic stage: a track-NN_L.trk file, or a folder that holds one stage
rbr2stj classic "C:/Games/RBR/Maps/track-54_M.trk" -o out/tanner
rbr2stj classic "C:/Games/RSF/Maps/101-Autiovaara II" -o out/autiovaara --all-variants

# a BTB stage folder
rbr2stj btb "C:/Games/RSF/RX_CONTENT/Tracks/Pirttikulma" -o out/pirttikulma

# a summary of any supported file or stage folder (.trk .dls .col .mat .fnc .lbs .x .xform)
rbr2stj info "C:/Games/RBR/Maps/track-54_M.col"
```

Options: `--variant M|N|E|O|S`, `--all-variants`, `--surface-states all|default` (nine wear/wetness maps or just
normal wear at the stage's default wetness), `--surface-table map.json` (adds grip numbers per surface id or name),
`--no-geometry` (line, timing and notes only), `--mesh-format auto|usda|usdc`, `--usdz` (single-file package, needs the
STJ tools), `--no-validate`.

After writing, the converter runs STJ's `validate.py --strict` when it can find the tools and prints the result.
The exit status is 0 on success, 1 if validation fails and 2 for an input problem.

### Try it without the game

No install is needed to try it: the tests build synthetic stages from the documented layouts.

```bash
python tools/make_synthetic.py out/synthetic
rbr2stj classic "out/synthetic/classic/101-Test Stage" -o out/test_stage --all-variants
rbr2stj btb "out/synthetic/btb/Synthetic BTB" -o out/test_btb
```

## Rules this tool follows

- It reads only files you may use. **RSF-encrypted files** (`01 a0 00 00`, `.ini` `01 a1 00 00`, and obscured `.lbs`
  `21 19 19 19`) are refused with a clear message and never decoded. XOR-encoded `.lsp` physics files are out of scope
  and left alone.
- It never writes into the game folder (a test checks the source folder is untouched).
- Original code, Apache-2.0. The formats were learned from the public notes of
  [reRBR](https://github.com/JodusNodus/rerbr) (GPL-3.0); no code from it is used, and each reader cites the note it follows.

## Tests

```bash
pip install -e .[test]
python -m pytest
```

Every binary reader has a test-only writer twin in `tests/fixtures/build.py`, so the suite runs without game data.
With a game install, `tests/test_real.py` converts every stage it finds:

```bash
RBR_DATA="C:/Games/RBR"  python -m pytest tests/test_real.py -s     # retail Maps/
RBR_RSF="C:/Games/RSF"   python -m pytest tests/test_real.py -s     # RSF Maps/<id>-<name>/ and RX_CONTENT/Tracks/
```

## Layout

```
src/rbr2stj/
  cli.py binio.py guard.py ini.py ir.py info.py errors.py
  readers/     chunks trk dls col mat fnc lbs stage_files rsf btb xfile xform surfaces_table
  convert/     classic btb pacenotes project surfaces edges
  writers/     stj_doc usd_mesh lbs_visual package
tests/         fixtures/build.py (test-only writers), test_*.py
docs/mapping.md
tools/         extract_surface_ids.py (cross-checks the surface table), make_synthetic.py
```

## Licence

Apache-2.0 (see `LICENSE`).
