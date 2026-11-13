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
_TINT_ORDER = {"M": "MNEO", "N": "NMEO", "E": "ENMO", "O": "OENM"}
_WETNESS_ORDER = {"dry": ("dry", "damp", "wet", ""), "damp": ("damp", "wet", "dry", ""), "wet": ("wet", "damp", "dry", "")}
_WEAR_ORDER = {None: ("", "new", "normal", "worn"), "new": ("new", "normal", "worn", ""),
               "normal": ("normal", "new", "worn", ""), "worn": ("worn", "normal", "new", "")}
_SET_NAME = re.compile(r"track-\d+_[mnseo]_textures", re.IGNORECASE)


def _member(e) -> str:
    """Member path of an index entry: inside the zip, or from the `*_textures` folder down for loose files."""
    name = e.name.replace("\\", "/")
    if e.zip_path is None:
        parts = name.split("/")
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].lower().endswith("_textures"):
                return "/".join(parts[i:]).lower()
    return name.lower()


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
    via: str = "name"            # "path": resolved like the game does; "name": guessed from the base name


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
    base: str = ""                                  # `track-225_o`: set for a classic stage; turns on the game's path rules
    ground: set = field(default_factory=set)        # lower-case stems of the `IsGroundTexture` textures
    rsf_remap: dict = field(default_factory=dict)   # RSF `TextureFilenameMap<id>.ini`, member path -> member path
    stock_map: dict = field(default_factory=dict)   # `Maps/TextureFilenameMap.dat`
    _idx: dict | None = field(default=None, repr=False)
    _full: dict | None = field(default=None, repr=False)
    _zf: dict = field(default_factory=dict, repr=False)

    def index(self) -> dict:
        if self._idx is None:
            key = (tuple(map(str, self.zips)), tuple(map(str, self.loose_dirs)))
            if key not in _INDEX_CACHE:
                _INDEX_CACHE[key] = _build_index((), key[0], key[1])
            self._idx = _INDEX_CACHE[key]
        return self._idx

    # -- the game's own rules (HedgeHog3D with RallySimFans), used when `base` is set -------------------------------
    def _members(self) -> dict:
        """Every file by its member path (`track-225_o_textures/damp/new/x.dds`, lower case, forward slashes)."""
        if self._full is None:
            full: dict = {}
            for entries in self.index().values():
                for e in entries:
                    full.setdefault(_member(e), e)
            self._full = full
        return self._full

    def _merged_map(self) -> dict:
        out = dict(self.rsf_remap)
        for src, tgt in self.stock_map.items():
            out.setdefault(src, self.rsf_remap.get(tgt, tgt))
        return out

    def _substitute(self, requested: str, name: str, wetness: str, wear: str | None) -> str:
        """RSF: when the requested path is neither a file nor a key of the stage's remap, the first existing sibling
        (tint, wetness, wear in that order of preference) stands in for it."""
        members = self._members()
        if not requested.startswith("track-") or requested in self.rsf_remap or requested in members:
            return requested
        prefix = self.base[:-1]
        for tint in _TINT_ORDER.get(self.base[-1:].upper(), ""):
            for wet in _WETNESS_ORDER[wetness]:
                for ear in _WEAR_ORDER[wear]:
                    if ear and not wet:
                        continue
                    cand = "/".join((f"{prefix}{tint.lower()}_textures", *filter(None, (wet, ear)), name + ".dds"))
                    hit = members.get(cand)
                    if hit is not None and hit.zip_path is None:
                        return cand                          # a loose file wins
                    if cand in self.rsf_remap:
                        return self.rsf_remap[cand]
                    if hit is not None:
                        return cand
        return requested

    def resolve(self, raw: str) -> _Entry | None:
        """The file the game loads for a stage `.ini` texture: `<base>_textures/dry[/new]/<stem>.dds` (ground textures
        keep the wear folder), the RSF substitute if that is missing, then the RSF and stock filename maps."""
        stem = re.split(r"[\\/]", raw)[-1]
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        stem = stem.lower()
        members, merged = self._members(), self._merged_map()
        ways = [("dry", "new"), ("dry", None)]
        if stem not in self.ground:
            ways.reverse()
        for wet, wear in ways:
            path = "/".join((f"{self.base}_textures", wet, *filter(None, (wear,)), stem + ".dds"))
            path = self._substitute(path, stem, wet, wear).lower()
            path = merged.get(path, path)
            e = members.get(path)
            if e is not None:
                return _Entry(e.zip_path, e.name, e.rank, "path")
        return None

    def find(self, raw: str) -> _Entry | None:
        if self.base and raw.lower().rsplit(".", 1)[-1] in {x[1:] for x in IMAGE_EXT}:
            hit = self.resolve(raw)
            if hit is not None:
                return hit
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
    report_borrowed: bool = True                   # off for the shared sky sets, which are looked up by name
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
            if self.report_borrowed and self.source.base and e.via != "path" and not _member(e).startswith(self.source.base[:-1]):
                # not where the game would load it from: found by name in some set (the first one that has the name)
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


def parse_rsf_remap(folder: Path) -> dict:
    """RSF `TextureFilenameMap<id>.ini`: `source<TAB>target`, `;` comments. A target that does not start with a texture
    set name (`damp\\new\\x.dds`) is a path inside the set of its source."""
    out: dict = {}
    for f in folder.glob("TextureFilenameMap*.ini"):
        for line in f.read_text(encoding="latin-1", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith(";") or "\t" not in line:
                continue
            src, tgt = (x.strip().replace("\\", "/") for x in line.split("\t", 1))
            if not _SET_NAME.fullmatch(tgt.split("/")[0]):
                tgt = f"{src.split('/')[0]}/{tgt}"
            out[src.lower()] = tgt.lower()
    return out


def parse_stock_map(path: Path) -> dict:
    """`Maps/TextureFilenameMap.dat`: u32 count, then `request\\0target\\0` pairs."""
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < 4:
        return {}
    count = int.from_bytes(data[:4], "little")
    parts = data[4:].split(b"\0")
    if len(parts) <= 2 * count:
        return {}
    out: dict = {}
    for i in range(count):
        src = parts[2 * i].decode("latin-1").replace("\\", "/").lower()
        out.setdefault(src, parts[2 * i + 1].decode("latin-1").replace("\\", "/").lower())
    return out


def for_classic(folder: Path, track: str, letter: str, remap_targets: list[str] | None = None,
                ground: set | None = None) -> TextureSource:
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
    return TextureSource(zips, loose, prefer, base=f"{track}_{letter}".lower(), ground={g.lower() for g in (ground or ())},
                         rsf_remap=parse_rsf_remap(folder), stock_map=parse_stock_map(maps / "TextureFilenameMap.dat"))


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
