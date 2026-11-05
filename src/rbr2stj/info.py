"""`rbr2stj info`: a short summary of any supported file or stage folder (like reRBR's stage-info), read-only."""

from __future__ import annotations

from pathlib import Path

from . import guard
from .errors import Rbr2StjError
from .readers import btb, col, dls, fnc, lbs, mat, stage_files, surfaces_table, trk, xfile, xform


def _trk(data: bytes) -> list[str]:
    t = trk.parse(data)
    d = t.driveline
    out = [f"driveline: {len(d)} points, distance {d[0].distance:.1f} .. {d[-1].distance:.1f} m"]
    for m in t.meshes:
        out.append(f"  mesh {m.name!r}: kind {trk.KIND_NAMES.get(m.kind, m.kind)}, surface "
                   f"{surfaces_table.name(m.material)}, {len(m.vertices)} vertices, {len(m.faces)} faces, "
                   f"{len(m.placements)} placements")
    return out


def _dls(data: bytes) -> list[str]:
    d = dls.parse(data)
    m = dls.markers(d)
    return [f"AnimData v{d.version}: sets {d.set_names}", f"start {m.start}, finish {m.finish}, end {m.end}, "
            f"splits {m.splits}", f"{len(m.notes)} pace-note keys, {len(d.triggers)} animation triggers, "
            f"{len(d.spheres)} sound spheres, named triggers {[n for n, _ in d.named_triggers]}"]


def _col(data: bytes) -> list[str]:
    c = col.parse(data)
    tri = sum(len(x.triangles) for x in c.cells)
    out = [f"collision: {len(c.cells)} cells, {tri} triangles, {len(c.puddles)} puddle quads, "
           f"{len(c.deep_water)} deep-water quads, brake wall: {'yes' if c.brake_wall else 'no'}"]
    if c.superseded_cells:
        out.append(f"  {c.superseded_cells} cell(s) overwritten by a later cell on the same graft")
    return out


def _mat(data: bytes) -> list[str]:
    m = mat.parse(data)
    return [f"{len(m.entries)} surface entries" + (f", {len(m.tail)} trailing bytes (CMAP?)" if m.tail else "")] + [
        f"  {e.name!r}: {len(e.runs)} runs" for e in m.entries]


def _fnc(data: bytes) -> list[str]:
    f = fnc.parse(data)
    return [f"{len(f.groups)} fence groups, textures {f.textures}"] + [
        f"  type {g.fence_type}, post {g.post_type}, {len(g.posts)} posts" for g in f.groups]


def _lbs(data: bytes) -> list[str]:
    parsed = lbs.parse(data)
    return [f"chunks {parsed.chunk_kinds}", f"start {parsed.start.position if parsed.start else None}",
            f"{len(parsed.animation_objects)} animation objects"]


def describe(path: Path) -> str:
    if not path.exists():
        raise Rbr2StjError(f"{path}: no such file or folder")
    lines = [str(path)]
    if path.is_dir():
        if btb.is_btb_folder(path):
            t = btb.open_folder(path)
            lines += [f"BTB stage {t.name!r}, physics {t.physics}, author {t.author}",
                      f"driveline {len(t.driveline)} points, {len(t.notes)} notes, {len(t.objects)} objects, "
                      f"{len(t.materials)} materials, {len(t.map_rows)} surface maps"]
        else:
            stems = stage_files.variants_in(path)
            if not stems:
                raise Rbr2StjError(f"{path}: neither a BTB stage folder nor a folder with track-<id>_<L>.trk files")
            for stem, letters in stems.items():
                lines.append(f"classic stage {stem}: variants {', '.join(letters)}")
        return "\n".join(lines)
    data = path.read_bytes()
    if guard.is_protected(data[:4]):
        raise Rbr2StjError(guard.refusal(path.name))
    ext = path.suffix.lower()
    handlers = {".trk": _trk, ".dls": _dls, ".col": _col, ".mat": _mat, ".fnc": _fnc, ".lbs": _lbs}
    if ext in handlers:
        lines += handlers[ext](data)
    elif ext == ".x":
        m = xfile.parse(data, path.name)
        lines.append(f"{len(m.vertices)} vertices, {len(m.triangles)} triangles, UVs: {m.uvs is not None}, "
                     f"materials {m.material_names}")
    elif ext == ".xform":
        lines.append(f"{len(xform.parse(data, path.name))} placement matrices")
    else:
        raise Rbr2StjError(f"{path.name}: not a supported file type (.trk .dls .col .mat .fnc .lbs .x .xform)")
    return "\n".join(lines)
