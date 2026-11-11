"""Find the images a stage refers to and copy them, unchanged, next to the package (opt-out with `--no-textures`).

RBR / RSF keep a stage's textures in `track-NN_L_textures.rbz` (a plain zip: `<dir>/dry|damp|wet|new|normal|worn/<name>.dds`),
in the same folders unpacked, or in another stage's zip (RSF's `TextureFilenameMap*.ini` remaps identical files to one
owner). A stage `.ini` names a texture as `Texture<N>=<path>.tga`; the game loads `<stem>.dds`. So a request is
resolved by base name: the stage's own textures first, then any other texture set found beside it, dry before the other
wetness folders. Nothing is ever written into the game folder; the output is the user's own conversion and the package
stays marked `redistributable: false`. The files are copied byte for byte (DDS and TGA are read natively by Blender and most USD viewers); Pillow is used only to
detect transparency.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = (".dds", ".tga", ".png", ".jpg", ".jpeg", ".bmp")
WETNESS_RANK = {"dry": 0, "damp": 1, "wet": 2}
WEAR_RANK = {"normal": 0, "new": 1, "worn": 2}     # a plate exists per wear state; the unworn default is `normal`
_INDEX_CACHE: dict[tuple, dict] = {}


def _keys(raw: str) -> list[str]:
    """Base names to look up for a request: the entry with its last extension swapped for each image extension."""
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    stem = base.rsplit(".", 1)[0] if "." in base and base.rsplit(".", 1)[1] in {e[1:] for e in IMAGE_EXT} else base
    return [stem + e for e in IMAGE_EXT] + [base]


@dataclass
class _Entry:
    zip_path: Path | None
    name: str                      # path inside the zip, or the file path
    rank: tuple = (9, 9)


def _build_index(roots: tuple[str, ...], zips: tuple[str, ...], loose_dirs: tuple[str, ...]) -> dict:
    idx: dict[str, list[_Entry]] = {}

    def add(zp, name):
        low = name.replace("\\", "/")
        base = low.rsplit("/", 1)[-1].lower()
        if base.endswith(IMAGE_EXT) or (base.startswith("shadow_") and base.endswith(".dat")):
            idx.setdefault(base, []).append(_Entry(Path(zp) if zp else None, name))

    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    if not n.endswith("/"):
                        add(z, n)
        except (zipfile.BadZipFile, OSError):
            continue
    for d in loose_dirs:
        for dirpath, _dirs, files in os.walk(d):
            for f in files:
                add(None, os.path.join(dirpath, f))
    return idx


@dataclass
class TextureSource:
    """Resolver for one stage. `zips` / `loose_dirs` are searched; `prefer` are lower-case path fragments that win."""
    zips: list[Path] = field(default_factory=list)
    loose_dirs: list[Path] = field(default_factory=list)
    prefer: list[str] = field(default_factory=list)
    _idx: dict | None = field(default=None, repr=False)
    _zf: dict = field(default_factory=dict, repr=False)

    def index(self) -> dict:
        if self._idx is None:
            key = (tuple(map(str, self.zips)), tuple(map(str, self.loose_dirs)))
            if key not in _INDEX_CACHE:
                _INDEX_CACHE[key] = _build_index((), key[0], key[1])
            self._idx = _INDEX_CACHE[key]
        return self._idx

    def find(self, raw: str) -> _Entry | None:
        idx = self.index()
        best = None
        for k in _keys(raw):
            for e in idx.get(k, ()):
                low = (str(e.zip_path or "") + "/" + e.name).replace("\\", "/").lower()
                own = 0 if any(p in low for p in self.prefer) else 1
                parts = low.split("/")
                wet = min((WETNESS_RANK[x] for x in parts if x in ("dry", "damp", "wet")), default=0)
                wear = min((WEAR_RANK[x] for x in parts if x in WEAR_RANK), default=0)
                score = (own, wet, wear, _keys(raw).index(k))
                if best is None or score < best[0]:
                    best = (score, e)
        return best[1] if best else None

    def read(self, e: _Entry) -> bytes:
        if e.zip_path is None:
            return Path(e.name).read_bytes()
        zf = self._zf.get(e.zip_path)
        if zf is None:
            zf = self._zf[e.zip_path] = zipfile.ZipFile(e.zip_path)
        return zf.read(e.name)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "texture"


@dataclass
class TextureExporter:
    """Copies requested textures, unchanged, to `<out>/textures/`, once each; remembers what it wrote and what failed."""
    source: TextureSource
    out_dir: Path
    done: dict = field(default_factory=dict)       # raw request -> (rel path from the package root, has_alpha) | None
    failed: dict = field(default_factory=dict)     # raw request -> reason
    borrowed: dict = field(default_factory=dict)   # raw request -> where it came from, when not the stage's own set
    _names: dict = field(default_factory=dict)     # file name -> source identity

    def export(self, raw: str):
        if raw in self.done:
            return self.done[raw]
        res = None
        e = self.source.find(raw)
        if e is None:
            self.failed[raw] = "not found in the stage's texture sets"
        else:
            low = (str(e.zip_path or "") + "/" + e.name).replace("\\", "/").lower()
            if self.source.prefer and not any(p in low for p in self.source.prefer):
                self.borrowed[raw] = (e.zip_path.name + ":" if e.zip_path else "") + e.name.replace("\\", "/")
            try:
                res = self._convert(raw, e)
            except Exception as ex:   # an unreadable DDS variant must not stop the conversion
                self.failed[raw] = f"{type(ex).__name__}: {ex}"
        self.done[raw] = res
        return res

    def _convert(self, raw: str, e: _Entry):
        """Copy the original file byte for byte (no re-encoding, no baking); Pillow only reads it to see whether it
        has real transparency, which decides cut-out opacity on the material."""
        from PIL import Image
        data = self.source.read(e)
        if e.name.lower().endswith(".dat"):
            return self._convert_shadow(e, data)
        has_alpha = False
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
            if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
                has_alpha = im.convert("RGBA").getchannel("A").getextrema()[0] < 250
        except Exception:
            pass                       # still copied; the material just has no cut-out
        orig = e.name.replace("\\", "/").rsplit("/", 1)[-1]
        ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "dds"
        stem = slug(orig.rsplit(".", 1)[0])
        ident = (str(e.zip_path), e.name)
        name, k = stem, 2
        while self._names.get(name, ident) != ident:
            name, k = f"{stem}_{k}", k + 1
        self._names[name] = ident
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{name}.{ext}").write_bytes(data)
        return (f"textures/{name}.{ext}", has_alpha)

    def _convert_shadow(self, e: _Entry, data: bytes):
        """`shadow_N.dat` (u32 width, u32 height, L8 texels: the game's shadow map) -> a greyscale PNG with the same
        texels. A change of container only; no texel is touched."""
        import struct
        from PIL import Image
        w, h = struct.unpack_from("<II", data)
        if w * h + 8 > len(data) or not (0 < w <= 16384 and 0 < h <= 16384):
            raise ValueError(f"shadow map header {w}x{h} does not fit {len(data)} bytes")
        orig = e.name.replace("\\", "/").rsplit("/", 1)[-1]
        stem = slug(orig.rsplit(".", 1)[0])
        track = slug(e.name.replace("\\", "/").split("/")[0]) if "/" in e.name.replace("\\", "/") else ""
        base = f"{track}_{stem}" if track else stem
        ident = (str(e.zip_path), e.name)
        name, k = base, 2
        while self._names.get(name, ident) != ident:
            name, k = f"{base}_{k}", k + 1
        self._names[name] = ident
        self.out_dir.mkdir(parents=True, exist_ok=True)
        Image.frombytes("L", (w, h), data[8:8 + w * h]).save(self.out_dir / f"{name}.png", optimize=True)
        return (f"textures/{name}.png", False)

    def summary(self) -> dict:
        return {"exported": sum(1 for v in self.done.values() if v), "missing_or_failed": dict(self.failed),
                "from_other_sets": dict(self.borrowed)}


def for_classic(folder: Path, track: str, letter: str, remap_targets: list[str] | None = None) -> TextureSource:
    """Texture sets around a classic stage folder: its own `track-NN_L_textures`, then every `*_textures` zip or
    folder in the same `Maps` directory (and `Textures` / `Generic` beside it in an RSF install)."""
    maps = folder.parent if folder.parent.name.lower() == "maps" else folder
    zips = sorted(set(folder.glob("*.rbz")) | set(maps.glob("*.rbz")) | set(maps.glob("*/*.rbz")))
    loose = [d for d in maps.glob("*_textures") if d.is_dir()] + [d for d in folder.glob("*_textures") if d.is_dir()]
    for extra in ("Textures", "Generic"):
        p = maps.parent / extra
        if p.is_dir():
            loose.append(p)
    prefer = [f"{track}_{letter}_textures".lower()] + [t.lower() for t in (remap_targets or [])]
    return TextureSource(zips, loose, prefer)


def remap_targets(folder: Path) -> list[str]:
    """Texture-set names RSF's `TextureFilenameMap<id>.ini` remaps this stage's files to (`track-13_o_textures`)."""
    out: list[str] = []
    for f in folder.glob("TextureFilenameMap*.ini"):
        for line in f.read_text(errors="replace").splitlines():
            if line.startswith(";") or "\t" not in line:
                continue
            tgt = line.split("\t")[1].replace("\\", "/").split("/")[0].strip().lower()
            # only a first component that names a texture set counts: `damp\new\x.dds` is a path inside a set, and as a
            # "set name" it matched the damp folder of every other stage's archive (Falstone got another stage's plate)
            if tgt and tgt.endswith("_textures") and tgt not in out:
                out.append(tgt)
    return out
