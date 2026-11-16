"""A stage and its reverse as one package: the reverse becomes a `layouts[]` entry (STJ spec 3.5).

RSF ships a reverse stage as a folder of its own (`353-Passo Valle Reverse` beside `352-Passo Valle`). When it is the same
ground run the other way, one STJ package describes both: the base stage is the document, the reverse is a layout with its own
timing, pace notes, start pose and edges. The ground stays the base stage's (spec 3.5, rule 4).

A reverse is merged only when the evidence is there; anything else stays a separate stage and the reason is reported:
- the name is the forward name plus a reverse tag (`Reverse`, `Reversed`, `Rever`, `Rev`), in the same folder;
- the `.col` (the ground) is byte for byte the same file;
- its line is the forward line the other way (starts near where the forward ends and ends near where it starts).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from . import Options

_TAG = re.compile(r"[ _-]*(reverse[d]?|rever|rev)(?![a-z])", re.IGNORECASE)
NEAR_M = 150.0            # a reverse starts and ends this close to the other end of the forward line
SAME_LINE_M = 0.05        # the reverse line is the forward line backwards when every point agrees to this
SAME_LENGTH_M = 0.01


def _bare(folder_name: str) -> str:
    return re.sub(r"^\d+-", "", folder_name)


def is_reverse_name(folder_name: str) -> bool:
    return bool(_TAG.search(_bare(folder_name)))


def _key(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _TAG.sub(" ", _bare(folder_name)).lower())


def find_reverse_folders(folder: Path) -> list[Path]:
    """Reverse-named sibling folders whose name, without the tag, is this stage's name."""
    folder = Path(folder)
    if is_reverse_name(folder.name) or not folder.parent.is_dir():
        return []
    key = _key(folder.name)
    if not key:
        return []
    return sorted(d for d in folder.parent.iterdir()
                  if d.is_dir() and d != folder and is_reverse_name(d.name) and "mirror" not in d.name.lower()
                  and _key(d.name) == key)


def find_forward_folder(folder: Path) -> Path | None:
    """For a reverse folder, the forward folder it belongs to by name (None when it is not a reverse)."""
    folder = Path(folder)
    if not is_reverse_name(folder.name) or not folder.parent.is_dir():
        return None
    key = _key(folder.name)
    for d in sorted(folder.parent.iterdir()):
        if d.is_dir() and d != folder and not is_reverse_name(d.name) and _key(d.name) == key:
            return d
    return None


def reverse_layout(forward, folder: Path, opt: Options):
    """Convert `folder` (a reverse stage) and return (layout dict, None) or (None, reason it is not merged)."""
    from ..errors import Rbr2StjError
    from ..writers.stj_doc import layout_entry
    from .classic import convert_classic
    ropt = Options(**{**opt.__dict__, "variant": None, "all_variants": False, "visual": False, "textures": False})
    try:
        rev = convert_classic(folder, ropt)
    except Rbr2StjError as e:
        return None, f"{folder.name}: {e}"
    if forward.file_hashes.get("col") != rev.file_hashes.get("col"):
        return None, f"{folder.name}: its ground (.col) differs from this stage's, so it is another map"
    if forward.origin_offset != rev.origin_offset:
        return None, f"{folder.name}: re-centred on another origin"
    fp, rp = forward.line.points, rev.line.points
    if not fp or not rp:
        return None, f"{folder.name}: no driveline"
    if math.dist(rp[0], fp[-1]) > NEAR_M or math.dist(rp[-1], fp[0]) > NEAR_M:
        return None, (f"{folder.name}: its line does not run the forward line backwards (starts {math.dist(rp[0], fp[-1]):.0f} m "
                      f"from the forward finish, ends {math.dist(rp[-1], fp[0]):.0f} m from its start)")
    same = (len(fp) == len(rp) and abs(forward.line.chainage[-1] - rev.line.chainage[-1]) <= SAME_LENGTH_M
            and all(math.dist(a, b) <= SAME_LINE_M for a, b in zip(fp[::-1], rp)))
    x = {"source": rev.source, "track": rev.x_rbr.get("track"), "variant": rev.info.letter,
         "chainage_offset": rev.x_rbr.get("chainage_offset"),
         "line": "the base line backwards" if same else "the stage's own line (not exactly the base line backwards)"}
    name = rev.info.name or f"{forward.info.name} reverse"
    return layout_entry("reverse", name, rev, same, x), None
