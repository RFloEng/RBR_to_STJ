"""Find a stage's files: loose folder (case-insensitive, as Windows matches them) or inside a `.rbz` (plain ZIP).

Naming: reRBR notes/formats.md "Stage files" and "RSF stages": a stage is `track-<id>_<L>` with extensions
`.trk .dls .col .mat .fnc .lbs .ini` (L in M N E O S); RSF puts them in `Maps/<id>-<name>/`.
Protected files (RSF-encrypted) are refused by `guard`, never decoded.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import guard
from ..errors import Rbr2StjError

REQUIRED = ("trk", "dls", "col", "mat")
OPTIONAL = ("fnc", "lbs", "ini")
LETTERS = "MNEOS"
STAGE_RE = re.compile(r"^(track-\d+)_([A-Za-z])$", re.I)


def in_dir(folder: Path, name: str) -> Path | None:
    """Entry of `folder` called `name`, ignoring case."""
    p = folder / name
    if p.exists():
        return p
    low = name.lower()
    try:
        for e in folder.iterdir():
            if e.name.lower() == low:
                return e
    except OSError:
        return None
    return None


@dataclass
class StageSet:
    """The files of one stage variant, loose or in a zip."""
    folder: Path
    track: str                      # "track-101"
    letter: str                     # "M"
    zips: list[zipfile.ZipFile] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return f"{self.track}_{self.letter}"

    def read(self, ext: str, required: bool = True) -> bytes | None:
        name = f"{self.stem}.{ext}"
        p = in_dir(self.folder, name)
        if p is not None and p.is_file():
            return guard.read_file(p)
        for z in self.zips:
            for info in z.infolist():
                if info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == name.lower():
                    return guard.check_bytes(z.read(info), name)
        if required:
            raise Rbr2StjError(f"{name}: not found in {self.folder}")
        return None

    def has(self, ext: str) -> bool:
        try:
            return self.read(ext, required=False) is not None
        except Rbr2StjError:
            return True   # protected counts as present so the refusal is reported, not "missing"


def parse_stage_name(name: str) -> tuple[str, str] | None:
    m = STAGE_RE.match(Path(name).stem if "." in name else name)
    return (m.group(1).lower(), m.group(2).upper()) if m else None


def variants_in(folder: Path) -> dict[str, list[str]]:
    """{track stem: [variant letters]} found by their `.trk` files (loose, or inside a `.rbz` zip) in `folder`."""
    found: dict[str, set[str]] = {}
    for e in folder.iterdir():
        low = e.suffix.lower()
        if low == ".trk":
            pn = parse_stage_name(e.stem)
            if pn:
                found.setdefault(pn[0], set()).add(pn[1])
        elif low == ".rbz" and not e.stem.lower().endswith("_textures"):
            try:
                with zipfile.ZipFile(e) as z:
                    names = [n.replace("\\", "/").rsplit("/", 1)[-1] for n in z.namelist()]
            except (zipfile.BadZipFile, OSError):
                continue
            for n in names:
                if n.lower().endswith(".trk"):
                    pn = parse_stage_name(n)
                    if pn:
                        found.setdefault(pn[0], set()).add(pn[1])
    return {k: sorted(v, key=lambda c: LETTERS.find(c) if c in LETTERS else 9) for k, v in found.items()}


def open_stage(path: str | Path, variant: str | None = None) -> tuple[StageSet, list[str]]:
    """Resolve `path` (a `track-NN_L.trk` file or a stage folder) to a StageSet and the variants available.

    A folder holding several track stems is ambiguous and is refused; a folder with one picks `variant`
    (default: the first of O, M, N, E, S found, the order RSF uses when nothing chooses).
    """
    p = Path(path)
    if p.is_file():
        pn = parse_stage_name(p.name)
        if not pn:
            raise Rbr2StjError(f"{p.name}: not a stage file name (expected track-<id>_<L>.trk)")
        folder, track, letter = p.parent, pn[0], pn[1]
        letters = variants_in(folder).get(track, [letter])
    elif p.is_dir():
        folder = p
        stems = variants_in(folder)
        if not stems:
            raise Rbr2StjError(f"{p}: no track-<id>_<L>.trk file found")
        if len(stems) > 1:
            raise Rbr2StjError(f"{p}: holds several stages ({', '.join(sorted(stems))}); name one .trk file")
        track = next(iter(stems))
        letters = stems[track]
        letter = next((c for c in "OMNES" if c in letters), letters[0])
    else:
        raise Rbr2StjError(f"{p}: no such file or folder")
    if variant:
        if variant.upper() not in letters:
            raise Rbr2StjError(f"variant {variant.upper()} not found for {track} (have {', '.join(letters)})")
        letter = variant.upper()
    zips = []
    for z in folder.iterdir():
        if z.suffix.lower() == ".rbz" and not z.stem.lower().endswith("_textures"):
            try:
                zips.append(zipfile.ZipFile(z))
            except (zipfile.BadZipFile, OSError):
                pass
    return StageSet(folder, track, letter, zips), letters
