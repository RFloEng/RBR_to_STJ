# rbr2stj — Richard Burns Rally stages → STJ

Plan for a Python program that converts Richard Burns Rally (RBR) stages into **STJ — Standard Track JSON** v0.3
(https://github.com/RFloEng/STJ-Standard-Track-Json). The file formats come from the open reimplementation
**reRBR** (https://github.com/JodusNodus/rerbr), which parses every RBR stage file.

Decisions already taken (2026-11-05):

| Decision | Choice |
|---|---|
| Language | **Python 3.12+** (3.14 installed here), with `usd-core` (`pxr`) and `jsonschema`, the same stack as STJ's own tools. Rust is not installed. |
| Licence | **Apache-2.0**, like STJ. reRBR is **GPL-3.0**: read its notes and code to learn the formats, but **do not copy or translate its code**. Write original Python from the format facts. Cite the source as a comment (`# format: reRBR notes/formats.md "Markers"`). |
| Scope v1 | **Classic stages** (retail and RSF community: `track-NN_<L>.{trk,dls,col,mat,fnc,lbs,ini}`) **and BTB stages** (`RX_CONTENT/Tracks/<folder>/`). |
| Test data | **No RBR install is available.** Every reader is tested on synthetic files that a test-only writer builds from the documented layouts. Real-file tests are skipped unless `RBR_DATA` / `RBR_RSF` point at an install. |

---

## 0. Ground rules (from STJ's README and converter plan)

- Read **only files the user owns**. Never decrypt, unpack protected archives or bypass protection.
  - RSF-encrypted files start `01 a0 00 00` (`.ini`: `01 a1 00 00`). Stop with a clear message ("encrypted by RallySimFans, not supported"). Do not try to decode.
  - `.lsp` buffers that do not start `((` are XOR-encoded. Physics `.lsp` files are **out of scope**; do not decode them.
- Output from game content gets `_metadata.licence.redistributable: false`.
- Write `_metadata.provenance = {type: "converted", converted_from: [{format: "rbr-classic" | "rbr-btb", tool: "rbr2stj", tool_version, source, date, lost: [...]}]}`. Every step that drops data adds a line to `lost`.
- Textures and scenery assets are **referenced by name or local path**, never embedded.
- Never write into the game folder.

## 1. Reference material to set up

Clone both repos into `reference/` (add `reference/` to `.gitignore`; never ship it):

```bash
git clone --depth 1 https://github.com/JodusNodus/rerbr reference/rerbr
```

```bash
git clone --depth 1 https://github.com/RFloEng/STJ-Standard-Track-Json reference/STJ
```

What to read, in this order:

| File | Why |
|---|---|
| `reference/STJ/spec/STJ_Spec.md` §4 (frames, s/t), §5, §6.3, §7.1–7.3, §7.5, §7.7–7.8, §9, §10, §11.1, §12 | What the output must be |
| `reference/STJ/schema/stj.schema.json` | Strict schema: unknown keys are errors; `x_*` and `_*` keys are allowed everywhere |
| `reference/STJ/proposals/stj_lessons_from_rbr_proposal.md` | STJ already added fields for RBR (chainage, tangents, surface maps with `wear`/`wetness`, `impact`, invisible drag edges, conditional puddles, `time_to_arrival`, weather options, sections). **This is the mapping's backbone.** |
| `reference/STJ/docs/converter_plan.md` §3 | STJ author's intent for RBR/BTB import |
| `reference/STJ/examples/coll_de_prova_gravel.usda`, `examples/make_examples.py` | A gravel stage with pace notes, surface maps, water, variants: the target shape |
| `reference/STJ/tools/stj_usd.py`, `tools/validate.py`, `tools/stj_core.py` | Embed/extract/package and validation. Call them; do not re-implement them. |
| `reference/rerbr/notes/formats.md` | **The binary layouts** of `.trk`, `.dls` markers, `.col`, `.mat`, `.fnc`, `.lbs`, RSF stages, pace-note plugin files |
| `reference/rerbr/notes/btb.md` | BTB folder files, `.x`/`.xform` meshes, frames, locale quirks |
| `reference/rerbr/crates/rbr-formats/src/{trk,dls,markers,col/mod,mat,fnc,lbs/*,ini,stage_files,stage_textures}.rs`, `rsf/stages.rs`, `pacenote/notes.rs` | Edge cases behind the notes (read, do not copy) |
| `reference/rerbr/crates/rbr-btb/src/{track,xfile,xform,scene}.rs` | BTB readers (read, do not copy) |
| `reference/rerbr/crates/rbr-formats/src/lsp_keys.rs` (`MaterialManager` keys) and `data/physics-lsp-fields.md` | Surface id → name table |
| `reference/rerbr/crates/rbr-render/src/hud/pace.rs`, `crates/rbr-sound/src/data.rs` | Pace-note tag meanings |

## 2. What an RBR stage contains (summary of the research)

### Classic stage (retail `Maps/` or RSF `Maps/<id>-<name>/`)

One stage = `track-<id>_<L>` with variant letter `L` ∈ {M, N, E, O, S} (morning, night, evening, overcast, …), each with:

| File | Content we use |
|---|---|
| `.trk` | Chunked file (`u32 header_len=8, u32 type, u32 subtype, u32 size, payload`). `(3,0x14)` **driveline**: `u32 count`, 32-byte points `{Vec3 position, Vec3 tangent, f32 distance, u16, u16}`, a Hermite spline, **Z up**. `(3,0x16)` **interactive collision meshes**: name, object kind (0 static, 1 cone, 2 block, 3 sign, 6 banner, 7 log, 8 log pole, 9 fence bar, 10 fence pole), material (surface id), dynamic flag, shape (none/box/sphere), vertices, faces, placements `{key, pos, scale, quat xyzw}`. |
| `.dls` | `MINAATAD` v18 AnimData. Animation set **`Driveline`**: keys `{u32 tag, u32 flags, f32 distance}` sorted by distance. Tags `0x15` start, `0x16` finish, `0x17` split (≤16), `0x18` end of stage, others **pace notes**. Same set: **animation triggers** `{object id, distance, lead}` (characters fire when `distance − lead·speed < x`). Also sound spheres (Vec4). |
| `.col` | Collision (`OC7R`): grafted cells of triangles. Triangle: 3 u16 vertex indices, attr A/B (three 5-bit per-vertex values; **attr A /31 = tyre blend weight between two surfaces**), u8 material 0, u8 material 1 (`.mat` run indices), 3 u8 per-vertex nibbles per material (low nibble /15 = `.mat` cell coordinate). Root lists: **A = wet puddle quads**, **B = deep water quads**. Optional **brake wall**: (inner, outer) xy point pairs with per-item flags (bit 14 sensor only, bit 15 respawn). |
| `.mat` | Surface grids: named entries (`dry/damp/wet` × `new/normal/worn`), each a list of 16×16 u8 runs of **surface ids**. Sampler reads row 0 only: `cells[clamp(floor(16·a))]`. |
| `.fnc` | Fences: groups `{fence type, post type, material, rail material, bounds}` of posts `{position, centre, half extent to next post, BGRA}` + texture names. |
| `.lbs` | Visual geometry, **Y up, left-handed** (= `.trk` frame with y/z swapped). v1 uses only `(2,11)` **car start** (Vec3 position, Vec3 axis-angle) and `(2,14)` animation objects (name, ids, Lua script names) for `x_rbr`. Visual geometry `(1,0)` is phase 6 (stretch). |
| `.ini` | `[TextureInfo]` texture lists (for `x_rbr` material names only). |
| `Maps/Tracks.ini` | `[MapNN] TrackName` (retail names). |
| RSF: `Tracks<id>.ini`, `TrackSettings<id>.ini`, `TextureFilenameMap<id>.ini`, `PS_*.ini` | Name, `Surface` (0 gravel, 1 tarmac, 2 snow), length km, `DefaultWetness`, `Particles`, `AnimationPath`; **weather rows** as section names `<id><letter>_[<cloud letter>_]<preset>_<cloud>`. Read `rsf/stages.rs` for the parse. |
| Pace-note plugin files | `[PACENOTES] count`, `[P<k>] type, distance, flag` (same three words as the `.dls` keys; extended ids in high bits). Optional extra source of notes. |

Surface ids name `physics.lsp` "surface types" keys: `Gravel_{Fine,Medium,Coarse}_{Deeper,Deep,Shallow}_{Dry,Damp,Wet}`, `Tarmac_{Fine,Medium,Coarse}_{Clean,Sprinkled,Covered}_{Dry,Damp,Wet}`, `Grass_{Hard,Medium,Soft}_*`, `Dirt_{Hard,Medium,Soft}_*`, `SnowOnGravel_*`, `SnowOnIce_*`, `Rough_*`, bushes, trees, `Rock_*`, `Trunk_*`, `Snowwall`, `Metal_Pole`, `Metal_Barrier`, `Bendable_Tree`, `Water_Surface`, `Spectator` (0xff), … The numeric ids are listed in `lsp_keys.rs`; **verify the id column against `data/physics-lsp-fields.md`** before building the table.

### BTB stage (`RX_CONTENT/Tracks/<folder>/`)

Plain INI files, numbers in the author's locale **but read in C locale** (commas are separators: `colBox=0,15, 1, 0,15` → (0, 15, 1); `LODOut=2679,576` → 2679). Text is bytes decoded lossy; sections/keys first match, case-insensitive.

| File | Content |
|---|---|
| `track.ini [INFO]` | name, physics (`gravel`/`tarmac`/`snow`), author, version, splashscreen, date, comment, length km, countrycode |
| `driveline.ini [DRIVELINE]` | `count`, `K<i>` = 8 floats (first 7 = position xyz, tangent xyz, distance; frame Z up like `.trk`) |
| `pacenotes.ini` | `[PACENOTES] count`, `[P<i>] type, distance, flag` (only the first `count`) |
| `spawn.ini [SPAWN]` | `m12 m13 m14` translation, `angleZ` yaw |
| `settings.ini` | `sunDirection` |
| `mat.ini` | `[MAP<i>] data0..data15` 16×16 surface-id grids; `texture=` names. Quirks: glued `69[MAP1]`, duplicate sections (first wins), missing rows read zeros |
| `materials.ini` | `MAT_<i>` names, per-material `effectName`, `techniqueName`, `diffuseTexture` |
| `objectlist.ini` | `[Object_<i>]`: `file`, `xform`, `Visible`, `CollisionModel` (-1/0 → ground collision, 1/2 → box collider), `colBox`, `LODIn`, `LODOut` |
| `Objects.zip` | `Objects/*.x` (**binary DirectX `xof 0303bin 0032`**, one Mesh, triangles, one material = a `materials.ini` section) and `*.xform` (raw row-major 4×4 f32, 64 bytes each, D3D order). **D3D frame: left-handed, Y up**. STJ = `(x, z, y)`. |
| `Textures.zip` | textures (referenced by name only) |

A BTB stage's surface per triangle: mesh's material → `materials.ini` `diffuseTexture` base name → first `mat.ini [MAP<i>]` whose `texture=` matches → map index (0 if none). Cell from UV: `nibble = (int)(u*15)`.

## 3. Mapping RBR → STJ

Frames: `.trk`/driveline/`.col` are **Z up**; the `.lbs`/BTB D3D frame is left-handed Y up and becomes STJ by `(x, y, z) → (x, z, y)`. **Verify the handedness on a synthetic stage** (a left-hand bend in the driveline must have a left pace note and positive `t` on the left). Re-origin to the driveline centroid when coordinates exceed 10 km and write `environment.origin_offset`.

| RBR | STJ | Notes |
|---|---|---|
| Driveline points | `reference.centerline.points` | `[x, y, z]` |
| Driveline `distance` | `reference.centerline.chainage` | Defines `s`. Subtract the first point's distance if it is not 0 and record the shift in `x_rbr.chainage_offset`; all marker `s` values shift by the same amount. |
| Driveline `tangent` | `reference.centerline.tangents` | STJ wants `dP/du` over a segment (u = 0…1). **Open question:** RBR's tangent may be per metre or per segment. Test: if `|tangent| ≈ 1`, scale by segment length. Keep raw values in `x_rbr.tangents_raw`. |
| (no width in RBR) | `reference.centerline.width {left: 3.5, right: 3.5}` with `_est: true` | Optional: estimate from `.col` road triangles later. |
| Marker `0x15` | `timing.start.s` | |
| Marker `0x16` | `timing.finish.s` | |
| Marker `0x17` ×n | `timing.split_points[] {id: "split<n>", s}` | |
| Marker `0x18` | `timing.stop.s` | End of stage |
| Pace-note keys | `race.pacenotes {scale: "ascending_1_6", language: "en", notes[]}` | Table below. Keep `x_rbr {tag, flags}` on every note for a lossless round trip. Do not write `distance_to_next` (derived). |
| Animation triggers | `x_rbr.triggers[] {object, s, lead}` | STJ has signal `time_to_arrival`; crowds get `when` only once characters are converted (later). |
| `.col` triangles | USD mesh `meshes/<stage>_collision.usda`, role `collision` | One `Mesh` per cell or merged; `upAxis Z`, `metersPerUnit 1` |
| `.col` + `.mat` | `surfaces.maps[]` | `palette` = surface ids used; per-face primvars `stj_surf_layers_0/1` (material 0/1 resolved through the `.mat` cell) and `stj_surf_weights` faceVarying (attr A /31); `contact: "blend"` |
| `.mat` entries (9 states) | one `surfaces.maps[]` entry per entry, with `when` on signals `wear` (new 0, normal 0.5, worn 1) and `wetness` (dry 0, damp 0.5, wet 1) | Or, if per-state primvars get too big, one map plus override files. Pick the first; measure. |
| Surface ids | `surfaces.library["rbr_<name>"] {class, _est: true, x_rbr: {id, name}}` | `class`: Gravel→`gravel`, Tarmac→`asphalt`, Grass→`grass`, Dirt→`dirt`, SnowOn*/Snowwall→`snow`, Rock→`rock`, Water_Surface→`water`, Metal→`metal`, trees/trunks/bushes→`other` + `impact.response: bendable/rigid/soft`. No grip numbers (grip lives in NGP physics, not the stage): `lost` says so. A user mapping table (`--surface-table file.json`) can supply `mf_scaling`/`mu`. |
| `.col` list A (puddles) | `water.bodies[] {kind: "puddle", outline, when: {signal: "wetness", op: "ge", value: 0.5}}` | Quads → outline polygon xy + `level` = mean z |
| `.col` list B (deep water) | `water.bodies[] {kind: "pond" \| "ford", outline, level}` | `ford` when it crosses the centerline |
| Brake wall | `edges[] {kind: "invisible", behaviour: "drag" \| "sensor" \| "respawn", side, s_start, s_end, t}` | Project the inner/outer points onto the reference line for `s`/`t`; keep raw points in `x_rbr` |
| `.trk (3,0x16)` meshes | `scenery.prototypes` (+ USD prims) and `scenery.instances` | kind → prototype physics: cone/block/sign/banner/log → rigid prop; fence bar/pole → `edges[] {kind: barrier, style: fence}` |
| `.fnc` groups | `edges[] {kind: "barrier", style: "tape" \| "fence", tearable: true}` + posts in `x_rbr` | fence type → style table (verify) |
| `.lbs (2,11)` / BTB `spawn.ini` | `race.spawns[] {id: "start", pose}` | |
| RSF `Tracks<id>.ini` / BTB `track.ini` | `track_info {name, type: "point_to_point", primary_surface, length, country}` | length km → m |
| `TrackSettings<id>.ini` weather rows | `environment.weather_options[] {variant, weather {condition}, weight}` | preset/cloud ids → `condition` |
| Variant letters M/N/E/O/S | `variants[]` override layers (`<stage>_<L>.usda`) | Each variant whose files differ gets an override with its own geometry/surface maps; time of day in `environment.default_time` |
| `DefaultWetness` | `environment.default_weather.water_depth` (estimate) | `_est` |
| Everything without a home | `x_rbr` on the nearest object | e.g. texture lists, Lua script names, particle file, sound spheres |

### Pace-note tags → STJ

Corners (left tags 0–5, right 6–11; `25` flat right, `26` flat left). STJ severity is `1` = slowest short of a hairpin … `6` = fastest.

| Tag (left/right) | RBR call | STJ |
|---|---|---|
| 0 / 11 | hairpin | `kind: hairpin` |
| 1 / 10 | 90 | `kind: square` |
| 2 / 9 | K | `turn`, severity 2 |
| 3 / 8 | medium | `turn`, severity 3 |
| 4 / 7 | fast | `turn`, severity 4 |
| 5 / 6 | easy | `turn`, severity 5 |
| 26 / 25 | flat | `turn`, severity 6, modifier `flat` |
| 12 | twisty | `twisty` |
| 13 | distance call | skip (derived); keep in `x_rbr.skipped` |
| 14 | narrows | `narrows` |
| 15 | wide out | `widens` |
| 16 | over crest | `crest` |
| 17 | ford | `water` |
| 18 | caution | `caution` |
| 19 | bump | `bump` |
| 20 | jump | `jump` |
| 21–24 | start/finish/split/end | timing, not notes |
| 27 | bridge | `bridge` |
| 28 | go straight | `straight` |
| 29–31 | keep right / left / middle (**verify order**) | `other` + `direction`, `call` |
| 32 | caution water | `water` + modifier `caution` |

Flag bits → modifiers: `0x1` narrows, `0x2` widens, `0x4` tightens, `0x20` dont_cut, `0x40` cut, `0x80` tightens_bad, `0x400` long, `0x2000` maybe. Bit `0x100` = sticky / no link; `0x4000` = extended id in bits 19–30; `0x8` = take in bits 16–18. Unknown bits stay in `x_rbr.flags`. Always fill `call` with the original wording ("K left", "medium right long").

## 4. Program structure

```
rbr2stj/
  pyproject.toml            # name rbr2stj, Apache-2.0, deps: usd-core, jsonschema; entry point rbr2stj
  LICENSE, README.md, .gitignore (reference/, out/, *.pyc)
  src/rbr2stj/
    cli.py                  # rbr2stj classic|btb|info ...
    binio.py                # little-endian reader with bounds checks and clear errors
    guard.py                # encryption detection, "own files only" checks
    ini.py                  # byte-lossy, case-insensitive first-match INI; C-locale float scan
    readers/
      chunks.py             # .trk/.lbs framing
      trk.py  dls.py  markers.py  col.py  mat.py  fnc.py  lbs.py
      stage_files.py        # find a variant's files, case-insensitive, loose or in .rbz (zip) — refuse encrypted
      rsf.py                # Tracks<id>.ini, TrackSettings<id>.ini weather rows
      btb.py                # track/driveline/pacenotes/spawn/settings/mat/materials/objectlist ini
      xfile.py  xform.py    # BTB binary .x and .xform
      surfaces_table.py     # surface id -> (name, class, response)
    ir.py                   # source-neutral dataclasses: Stage, Driveline, Marker, Note, CollisionMesh, SurfaceMap, Water, Edge, Prop, Weather
    convert/
      classic.py  btb.py    # files -> IR
      pacenotes.py          # tag/flags -> STJ note
      project.py            # point -> (s, t) on the reference line (uses chainage + Hermite)
    writers/
      stj_doc.py            # IR -> STJ dict (provenance, lost list, x_rbr)
      usd_mesh.py           # collision/visual meshes, GeomSubsets, stj_surf_* primvars
      package.py            # root .usda with embedded doc (call STJ tools/stj_usd.py embed), variant layers
  tests/
    fixtures/build.py       # test-only WRITERS for every binary format -> synthetic stages
    test_*.py               # per reader, per mapping, end to end
```

CLI:

```text
rbr2stj classic <path/to/track-NN_M.trk | stage folder> [--variant M] [--all-variants] -o out/<name>/
rbr2stj btb <RX_CONTENT/Tracks/<folder>> -o out/<name>/
rbr2stj info <file>                # summary of any supported file, like reRBR's stage-info
  common: --surface-table map.json  --no-geometry  --validate (runs STJ validate.py --strict)
```

Output: a **folder package** `out/<name>/<name>.usda` (root layer with the STJ document in `customLayerData["stj"]`), `meshes/*.usda`, variant layers, and `<name>.stj.json` (extracted, for diffing). `.usdz` packaging through STJ's `stj_usd.py package` as an option.

## 5. Phases (each ends green: tests pass, output validates with `--strict`)

**P0 — Scaffold and fixtures.** Package, CLI skeleton, `binio`, `ini`, `guard`. `tests/fixtures/build.py` writes synthetic `.trk` (driveline + one prop mesh), `.dls` (Driveline set with start, 2 splits, finish, end, ~10 notes, 1 trigger), `.mat`, `.col` (small road strip, two surfaces, one puddle quad, one deep quad, a short brake wall), `.fnc`, `.lbs` (only `(2,11)`), RSF ini files, and a BTB folder (inis + a tiny binary `.x` + `.xform` in a zip). Done: fixtures build; readers' round-trip tests pass.

**P1 — Line, timing, notes (classic and BTB).** `.trk` driveline + `.dls` markers → `reference`, `timing`, `race.pacenotes`; BTB `driveline.ini` + `pacenotes.ini`. Minimal STJ document embedded in a `.usda`. Done: `validate.py --strict` passes; markers lie on the line; notes sorted; left bend gives left notes.

**P2 — Collision and surfaces.** `.col` → USD collision mesh (cells merged, vertices deduplicated per cell), `.mat` → surface maps with `wear`/`wetness` conditions, surface library from the id table, puddles/deep water → `water.bodies`, brake wall → invisible edges. BTB: `objectlist.ini` `CollisionModel 0` visible objects + `.x`/`.xform` → collision mesh, surface from `mat.ini` via texture name; `CollisionModel 1/2` boxes → props. Done: validator's primvar/material cross-checks pass.

**P3 — Props and fences.** `.trk (3,0x16)` meshes → prototypes + instances (USD prims, physics by kind); `.fnc` → edges + posts in `x_rbr`.

**P4 — Metadata, weather, variants.** `track_info`, `primary_surface`, length, spawn pose, weather options from `TrackSettings`, one override layer per extra variant letter, `x_rbr` extras (textures, Lua scripts, particles, sound spheres, triggers).

**P5 — BTB visual meshes.** `.x` meshes placed by `.xform` → USD visual meshes, one prototype per `.x` and instances per placement (PointInstancer or instanceable prims, not expanded), materials named after `materials.ini` sections with the diffuse texture path as a reference. LOD in/out → `geometry.meshes[].lod` where it fits, else `x_btb`.

**P6 (stretch) — Classic visual geometry.** `.lbs (1,0)` render chunks → USD visual meshes with vertex colour and UVs; texture names resolved through the stage `.ini` and `TextureFilenameMap`. Only if the layouts in `lbs/*.rs` are enough; otherwise add it to `lost`.

**P7 — Polish.** README with examples, `lost` lists complete, `--validate` default on, a `docs/mapping.md` generated from the tables above.

## 6. Testing without an install

- Each reader has a **writer twin in `tests/fixtures/`** (test-only code) and a round-trip test: write → read → equal.
- Edge cases from the notes become tests: `.col` graft paths and later-cell-wins; `.mat` name selection by wear/wet words; RSF encrypted magic → refused; BTB glued `69[MAP1]`, duplicate sections, C-locale commas; `.x` without UVs; `.xform` length not a multiple of 64 → error.
- End-to-end: synthetic classic stage and synthetic BTB folder → STJ package → `python reference/STJ/tools/validate.py <out>.usda --strict` returns 0.
- Real data (optional): `RBR_DATA=<install>` / `RBR_RSF=<install>` enable `tests/test_real.py`, which converts every stage found and reports failures. Skipped by default.

## 7. Open questions to settle while building (write the answer into `docs/mapping.md`)

1. Driveline tangent scaling (per metre vs per segment) — see §3.
2. Driveline `distance`: 3D arc length or x/y? (STJ `s` is x/y, but chainage overrides it; just keep it and let the validator's 1% check report.)
3. Exact handedness of `.trk` vs `.lbs` vs BTB D3D frames — confirm with a synthetic left bend and BTB evidence in `notes/btb.md` ("Frame").
4. Surface id table: confirm the id column in `lsp_keys.rs` against `data/physics-lsp-fields.md`.
5. Pace-note tags 29–31 order (keep right/left/middle) and 15 (wide out).
6. `.fnc` fence type numbers → tape / net / fence.
7. Variant layers: are geometry and `.col` identical across M/N/E/O for most stages? If yes, share meshes and override only presentation.
8. Size: a full stage `.col` may be ~1 M triangles; write `.usdc` (binary) for meshes if `.usda` gets too large, but keep the root layer `.usda`.

## 8. Lost in conversion (start of the `lost` list)

- Grip and all tyre physics (they live in NGP/`physics.lsp`, not in the stage)
- NGP ruts and deformation
- Baked vertex light, shadow maps and RBR shaders
- Lua character scripts and animations (names only, in `x_rbr`)
- Particle systems (file name only)
- Visibility cells and draw lists
- Classic visual geometry until P6
- RSF-encrypted stages (refused)
