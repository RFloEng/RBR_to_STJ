# RBR → STJ mapping

How `rbr2stj` turns Richard Burns Rally stage files into STJ v0.3. Format facts come from the reRBR notes
(`notes/formats.md`, `notes/btb.md`) and the STJ spec and its accepted proposal `stj_lessons_from_rbr_proposal.md`.
Each reader's docstring cites the exact note. The answers to the open questions of `PLAN.md` §7 are in
[Open questions](#open-questions-answered) with their evidence.

## Frames and units

| Source | Frame | To STJ (right-handed, Z up, metres) |
|---|---|---|
| `.trk`, driveline, `.col`, `.fnc` | right-handed, Z up | unchanged |
| `.lbs` | left-handed, Y up (the `.trk` frame with y and z swapped) | `(x, y, z) → (x, z, y)`; triangles reversed |
| BTB `.x`, `.xform`, `spawn.ini` | Direct3D: left-handed, Y up, row vectors `p·M`, translation in elements 12–14 | `(x, y, z) → (x, z, y)`; triangles reversed |
| BTB `driveline.ini` | Z up like `.trk` | unchanged |

- A car heading `yaw` about lbs/D3D Y (forward `(sin yaw, 0, cos yaw)`) becomes STJ heading `π/2 − yaw`.
- Reversing the winding after the axis swap keeps the outward normals right; classic `.col` winding is kept as stored.
- When any driveline coordinate exceeds 10 km the whole stage is re-centred on the driveline centroid and
  `environment.origin_offset` is written (`x_original = x + origin_offset`).
- The first driveline point's stored distance is subtracted from every `s` (`x_rbr.chainage_offset` keeps it).

## Reference line, timing, notes

| RBR | STJ | Notes |
|---|---|---|
| driveline points | `reference.centerline.points` | |
| driveline `distance` | `reference.centerline.chainage` | defines `s`; falls back to recomputed arc length (and says so in `lost`) if it is not strictly increasing |
| driveline `tangent` | `reference.centerline.tangents_out` / `tangents_in` | one per segment, written as stored (`tangents_in[i + 1] = tangents_out[i]`), so the game's own curve is reproduced exactly (spec 7.1). BTB stores about unit-length directions: they are scaled to the chord of their segment (`diagnostics.tangents_scaled_to_chord`); raw copy in `x_rbr.tangents_raw` |
| no width | `centerline.width {3.5, 3.5, _est}` | placeholder |
| marker `0x15` / `0x16` / `0x17` / `0x18` | `timing.start` / `finish` / `split_points[split<n>]` / `stop` | the last key of a tag wins; at most 16 splits |
| BTB notes with tags 21–24 | the same four | BTB's `pacenotes.ini` carries the keys the plugin builds into the `.dls` |
| other keys | `race.pacenotes.notes` | table below; `x_rbr {tag, flags}` on every note; `distance_to_next` never written |
| `TrackSettings` `SunDir`, `Diffuse*`, `Ambient*` | `environment.lighting.sun.direction` (negated, axes swapped: towards the sun, Z up), `sun.color`, `ambient` | one set per weather; the first weather of the base letter is in the document, every other weather is a `weather` variant (`<name>_wx_N.usda`) patching `environment.sky` and `.lighting`, referenced by `weather_options[].variant` |
| `.dls` sound spheres | `audio.emitters[]` with no `sound`, `max_distance` = the radius word, `x_rbr` (the event name is not in the stage files) | |
| `.lbs (1,6)` objects named *CLOCK* / *FLAG* | `animated_objects[]` `kind` clock / flag, `target` = the placed prims, no `time_range`, `x_rbr` | the game's script moves them |
| copied textures | `assets[]` `{id, uri, role: texture, sha256, licence.redistributable: false, source}` | |
| animation triggers | `x_rbr.triggers[] {object, s, lead}` | also plain triggers, named triggers, sound spheres, animation objects (name, ids, script names) |
| `.lbs (2,11)` / BTB `spawn.ini` | `race.spawns[start].pose` | |
| RSF `Tracks<id>.ini` | `track_info` (name, `primary_surface`, `length`, country), author | `Surface` is **0 tarmac, 1 gravel, 2 snow** (see Q-notes) |
| `TrackSettings<id>.ini` rows | `environment.weather_options[]` | only rows of letters present in the package; `variant` for the extra letters |
| `DefaultWetness` damp / wet | `environment.default_weather.water_depth` (0.0003 / 0.001 m, `_est`) | |
| variant letters | `variants[]` + `<name>_<l>.usda` override layers | `default_time.local_time` by letter: M 09:00, E 18:00, N 22:00, O 12:00 (`_est`) |

### Pace-note tags → STJ

Severity: 1 = slowest short of a hairpin … 6 = fastest still called.

| Tag (left / right) | RBR call | STJ |
|---|---|---|
| 0 / 11 | hairpin | `hairpin` |
| 1 / 10 | 90 | `square` |
| 2 / 9 | K | `turn`, severity 2 |
| 3 / 8 | medium | `turn`, severity 3 |
| 4 / 7 | fast | `turn`, severity 4 |
| 5 / 6 | easy | `turn`, severity 5 |
| 26 / 25 | flat | `turn`, severity 6, modifier `flat` |
| 12 | twisty | `twisty` |
| 13 | distance call | skipped (derived), listed in `x_rbr.notes_skipped` |
| 14 / 15 | narrows / wide out | `narrows` / `widens` |
| 16 | over crest | `crest` |
| 17 | ford | `water` |
| 18 | caution | `caution` |
| 19 / 20 | bump / jump | `bump` / `jump` |
| 21–24 | start / finish / split / end | timing, not notes |
| 27 / 28 | bridge / go straight | `bridge` / `straight` |
| 29 / 30 / 31 | keep right / left / middle | `other` + `direction` (not for middle) + `call` |
| 32 | caution water | `water` + modifier `caution` |
| ≥ 0xfea (plugin settings) | CALLOUT_*, INTO, AND ... | skipped, listed in `x_rbr.notes_skipped` |

Flag bits → modifiers: `0x1` narrows, `0x2` widens, `0x4` tightens, `0x20` dont_cut, `0x40` cut, `0x80` tightens_bad,
`0x400` long, `0x2000` maybe, `0x10` `x_flag_0x10`, `0x800` `x_plus`, `0x1000` `x_minus`. `0x100`, `0x8` (take) and the
extended-id bit `0x4000` stay in `x_rbr` (`unmapped_flags`, `extended_id`). `call` keeps the original wording
(`K left`, `medium right long`).

## Collision, surfaces, water

- **Mesh**: `.col` cells are merged into one USD `Mesh` (vertices stay per cell, no cross-cell welding). A later cell on
  the same graft overwrites an earlier one, as the game does (counted in `lost`).
- **Surface under a contact**: the game blends two surfaces per contact: `surface = cells[floor(16·a)]` of `.mat` run
  `mat[0]` / `mat[1]` (`a` the interpolated per-vertex nibble / 15), weighted by `attr A / 31`.
  We sample that at the three corners and keep the three strongest surfaces per face (`vertex_top3`): uniform int
  primvars `stj_surf_layers_<state>_0..2` and a `faceVarying` float2 `stj_surf_weights_<state>`; `contact: blend`.
  A surface change inside one triangle is reduced to its corner values (`lost`).
- **States**: one `surfaces.maps[]` entry per `.mat` entry selected by the first entry whose name contains the wear word
  (new/normal/worn) and the wetness word (dry/damp/wet). The map's `when` is on the `wear` and `wetness` signals
  (wear < 0.25 new, < 0.75 normal, else worn; the same cuts for wetness). Identical states merge into one map
  (`any` of their conditions; one unconditional map when all nine are equal). BTB has no states (nine identical entries).
- **Library**: `surfaces.library["rbr_<name>"]` per id used, `class` from the name (`Gravel*` gravel, `Tarmac*` asphalt,
  `Grass*` grass, `Dirt*`/`Rough*` dirt, `SnowOn*`/`Snowwall*` snow, `Rock*` rock, `Water_Surface` water, `Metal*` metal,
  trees/trunks/bushes `other` with `impact.response` rigid/soft/bendable), `_est: true`, `x_rbr {id, name}`.
  **No grip numbers**: they live in NGP/`physics.lsp`. `--surface-table` merges user numbers per id or name.
- **Water**: list A quads → `water.bodies[puddle]` with `when wetness ≥ 0.5`; list B quads → `ford` (a path body, width =
  short side, depth 0.15 m `_est`) when the reference line passes through the quad, else `pond` (polygon, `level` = mean z).
- **Brake wall** → `edges[kind: invisible]`: items without flags `drag` (`drag_coefficient` 0.5, `_est`, a placeholder: the
  files hold no number), bit 14 `sensor`, bit 15 `respawn`; consecutive equal items merge; `t`/`s` by projection onto the
  reference line. The reading "item k = the segment between point pairs k and k+1" is inferred (no retail file at hand).
- **BTB**: objects with `CollisionModel=0` and `Visible` ≠ 0 give faces (one copy per `.xform` matrix); surface per vertex
  = `mat.ini` map row 0 cell `int(u·15) & 15`, map chosen mesh material → `materials.ini` section → `diffuseTexture`
  base name → first map whose `texture=` matches (0 if none). `CollisionModel` 1/2 → static box props sized by `colBox`
  (x ± a/2, y ± c/2, z from −0.05·b to b; scale grouped into prototypes, heading from the matrix, tilt dropped and counted).

## Scenery, fences, visual geometry

| RBR | STJ |
|---|---|
| `.trk (3,0x16)` kind 1 cone, 2 block, 7 log | `scenery.prototypes` class `prop`, `physics.mode: movable` with an estimated mass (`_est`) |
| kind 3 sign, 6 banner | class `sign`, `breakable` |
| kind 8 log pole / 9–10 fence bar, pole | class `pole` / `fence` (props, not edges: placements carry no `s`) |
| kind 0 static, by surface id | `bush` / `tree` (trunk collision) / `rock` / `pole` / `prop`; `Bendable_Tree` → `physics.mode: bendable` (stiffness `_est`) |
| `.fnc` group | `edges[kind: barrier]`, `style` tape (height 0.074) or fence (0.9), `tearable` from the type table, posts in `x_rbr` |
| BTB `.x` + `.xform` | `meshes/<name>_visual.usda`: one prototype `Mesh` per `.x` under a `PointInstancer` (a plain transform for one placement); materials named after the `materials.ini` section with `stj:diffuseTexture`; the image from `Textures.zip` is copied unchanged to `textures/` and bound (UsdPreviewSurface + UsdUVTexture, cut-out opacity when it has alpha); `LODIn/LODOut` as `stj:lodIn/lodOut` and in `x_btb.objects` |
| classic `.lbs (1,0)` | `meshes/<name>_visual.*`: a `Mesh` per (block, view slot), a `GeomSubset` + material per render chunk (texture 0 shaded, a second texture copied and recorded as `stj:texture2File`), `st`, `st1`, `st_specular`, `st_shadow`, normals (slots 6, 8) and `displayColor` (baked light, never applied to the material); far-LOD chunks (draw list 3) in `<name>_visual_lod1.*`, a `lod` mesh of the near one (`switch_distance` 600 m, the renderer's documented value); the mesh entry carries `baked.vertex_colour {displayColor, light}`; cutout materials make their mesh `doubleSided` (spec 7.7) |
| classic `.lbs (1,1) (1,3) (1,6) (1,7) (1,8)` | `meshes/<name>_extras.*` (lanes, interactive, clouds, sky), `<name>_reflection.*` (a mesh with `reflective: true`) and `<name>_water.*` (a non-physical `water.bodies[]` entry, `physical: false`): object lanes (trees, grass; alpha test 60/255), the stage's sky dome (`SUPERBOWL.KSH` backdrop), interactive objects (a class prototype per draw plus one referenced Xform per stored matrix), reflection and water faces; unknown words, keys and boxes kept in `stj:extra` |
| shared `Clouds.lbs`, `skyboxes.lbs` | `meshes/<name>_clouds.*`, `_skyboxes.*`: every cloud / sky mesh; only the one the first weather's `CloudName` / `SkyboxName` selects is visible |
| textures | `textures/`: the original `.dds`/`.tga` bytes (never re-encoded) found by base name in the stage's `*_textures.rbz`, the remap targets, other texture sets in `Maps`, dry and unworn (`normal`) first; `--no-textures` switches it off |

## Package layout

See `README.md`. The root layer is Z-up, 1 m per unit, references the mesh layers under `/track`, and carries the
STJ document in `customLayerData["stj"]`. Mesh layers are `.usda` up to 50,000 faces and `.usdc` above (`--mesh-format`).

## Open questions answered

1. **Tangent scaling.** *Per segment.* The game builds the driveline with a cubic Hermite spline that uses the stored
   tangent unscaled: reRBR `crates/rbr-physics/src/ngp/drive_line.rs` `hermite_point`
   (`p0·(2t³−3t²+1) + v0·(t³−2t²+t) + p1·(−2t³+3t²) + v1·(t³−t²)`) and `engine_driveline/drive_line.rs` `hermite`. That is
   exactly STJ's `dP/du` over a segment, so tangents are written as stored. Every conversion records
   `x_rbr.diagnostics.tangent_to_chord_ratio {min, median, max}` (the validator warns outside 0.3–3), so a real stage
   confirms it at once. Test: `test_line_chainage_and_tangents`.
2. **Driveline `distance`: 3D or x/y.** **Still open: needs real data.** Without a game file the stored distance cannot be
   compared with a real stage's geometry. It is kept as the chainage (which defines `s`), and every conversion writes
   `x_rbr.diagnostics.chainage_vs_geometry {stored_length, xy_length, length_3d}`: the first real stage answers it. STJ's
   validator compares chainage against the x/y length with a 1 % tolerance. Test: `test_open_question_2_...`.
3. **Handedness.** `.trk`, driveline, `.col` and `.fnc` are the STJ frame already; `.lbs` is `.trk` with y/z swapped and
   left-handed (formats.md "Stage geometry"); BTB objects and spawn are D3D (btb.md "Drawing" → "Frame": the evidence is
   the placement boxes enclosing the driveline once `(x, z, y)` is applied, and the spawn lying ahead of `K0` along its tangent).
   Internal check built into every conversion: each left/right note is compared with the heading change of the line
   after it (`x_rbr.diagnostics.pacenotes.direction_vs_geometry {agree, disagree, unclear}`); a mirrored stage would show
   `disagree ≫ agree`. On the synthetic stage a left bend carries left notes and positive `t` is on the left
   (`test_left_bend_gives_left_note_and_handedness_checks_out`, `test_mirrored_stage_is_flagged`,
   `test_refline_projection_and_left_positive_t`). The synthetic check is circular for the frame itself; the real
   evidence is the btb.md note and the diagnostic on real stages.
4. **Surface id table.** The id column of `crates/rbr-formats/src/lsp_keys.rs` (`MaterialManager` keys) and the id rows
   of `data/physics-lsp-fields.md` agree on all 134 entries (`python tools/extract_surface_ids.py` prints
   `differences: {}`). `readers/surfaces_table.py` is generated from them.
5. **Pace-note tags 29–31 and 15.** 29 keep right, 30 keep left, 31 keep middle: reRBR `rbr-gameplay/src/markers.rs`
   `slot_word_id` maps them to bank words `0x34 KEEPRIGHT`, `0x35 KEEPLEFT`, `0x36 KEEPMIDDLE` (`rbr-sound/src/data.rs`).
   Tag 15 maps to word `0x2d WIDEOUT` → STJ `widens`. Also from there: 25 = flat **right**, 26 = flat **left**, tag 32
   shares word `0x13` with caution. Test: `test_keep_calls_order_and_caution_water`.
6. **`.fnc` fence types.** The file's fence type indexes the 15-row table `TYPES` (`g_fenceTypes`) in reRBR
   `rbr-render/src/fences/cloth/mod.rs`: height 0.074 m is a tape (types 0, 1, 4, 6, 8, 10, 12, 13, 14), 0.9 m a net/fence
   (2, 3, 5, 7, 9, 11); the row's flag (non-zero) makes the spans tear (all but 2, 3, 12, 13, 14). STJ has one fence style,
   so nets are `fence`. Caveat: this reads the renderer's table, not a documented file field.
7. **Variants across M/N/E/O.** *Handled either way; the real ratio needs real data.* Each letter is converted on its own.
   If a letter's `.col` and `.mat` (resp. `.trk`, `.lbs` + `.ini`) are byte-identical to the base's, the mesh layer is
   reused and the override patch holds only presentation (`test_open_question_7_identical_files_share_the_mesh`);
   otherwise it gets its own mesh and the patch is the RFC 7396 difference of the two documents
   (`test_all_variants_make_override_layers`). For BTB stages the five files are shared by construction (btb.md "Playing":
   one variant, the files do not depend on the weather).
8. **Size.** Measured on a synthetic strip (regular data, so a lower bound for compression), 64,872 collision
   triangles, nine surface states: `.usda` 21.7 MB, `.usdc` 2.1 MB; one state: `.usda` 4.6 MB, `.usdc` 2.1 MB.
   Conversion 7 s (≈ 110 µs per face, nine states). Extrapolated to a 1 M-triangle `.col`: `.usdc` tens of MB, `.usda`
   ≈ 330 MB, minutes to convert. The default mesh format is therefore `.usdc` above 50,000 faces (root layer always `.usda`),
   and `--surface-states default` writes one state when nine are too many.
   (Decision: nine primvar sets in one mesh, not one map plus override files; the measurement above is the cost.)

Further format notes found while building:

- `Tracks<id>.ini` `Surface` is 0 tarmac, 1 gravel, 2 snow in reRBR's parser (`rsf/stages.rs`); `PLAN.md` had 0 gravel,
  1 tarmac. We follow the parser. BTB `track.ini` uses the word `physics=gravel|tarmac|snow`.
- An obscured `.lbs` (`21 19 19 19`, RSF stage 500) is refused like an encrypted file.
- `.dls` animation sets are found by name (`Driveline`, case-insensitive), not by position.
- BTB `length=` may use a decimal comma; it is informational, so the stage length comes from the line instead.

## `lost`: what is dropped

Every conversion lists these in `_metadata.provenance.converted_from[].lost`; stage-specific lines are appended.

- Grip and all tyre physics (they live in NGP/`physics.lsp`); surfaces carry class and RBR id only
- NGP ruts and deformation state
- RBR shaders and the exact blend of the two textures (classic: baked vertex colour is kept as `displayColor`, not applied; BTB: effect and
  technique names are kept on the materials); `shadow_N.dat` shadow maps and specular maps are not found in RSF installs
- Lua character scripts and animations (names only, in `x_rbr`)
- Particle systems (file name only)
- Visibility cells (2,10) and containers (2,15/18); draw lists are kept as `stj:drawList` / far-LOD meshes
- Classic vertex words past the documented offsets (kept only as the raw stride in `stj:stride`); lane sway parameters
- `.col` cells overwritten by a later graft; surface changes inside one triangle (sampled at the corners)
- Tilt of BTB box colliders; non-uniform scale of classic props (kept raw in `x_rbr`)
- BTB weather and sky (BTB borrows map 41's rows)
- Not converted at all: **RSF-encrypted stages** are refused

## Estimates and placeholders (all marked `_est`)

Road width (3.5 m per side), brake-wall drag coefficient (0.5), ford depth (0.15 m), prop masses, bendable stiffness,
default water depth, time of day per variant letter, surface classes.
