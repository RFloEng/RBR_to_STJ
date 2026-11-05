"""BTB (Bob's Track Builder) stage folders: `RX_CONTENT/Tracks/<folder>/`.

Format: reRBR notes/btb.md "Sources", "Built files" and "Meshes" (plain INI files; numbers are written in the
author's locale but READ in the C locale, so commas separate; text is lossy bytes; sections and keys first match,
case-insensitive; `mat.ini` quirks: glued `69[MAP1]`, duplicate sections (first wins), missing rows read zeros).

Files: track.ini [INFO], driveline.ini [DRIVELINE] (count, K<i> = 8 floats), pacenotes.ini [PACENOTES] (count,
[P<i>] type/distance/flag; only the first `count`), spawn.ini [SPAWN] (m12 m13 m14, angleZ), settings.ini
[SETTINGS] sunDirection, mat.ini [MAPS] count + [MAP<i>] data0..data15 + texture=, materials.ini [MATERIALS]
MAT_<i> + one section per material, objectlist.ini [OBJECTS] + [Object_<i>], Objects.zip (Objects/*.x, *.xform),
Textures.zip.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import guard, ini
from ..errors import FormatError, Rbr2StjError
from . import xfile, xform
from .stage_files import in_dir


@dataclass
class DrivelinePoint:
    position: tuple[float, float, float]
    tangent: tuple[float, float, float]
    distance: float


@dataclass
class Note:
    tag: int
    distance: float
    flags: int


@dataclass
class BtbObject:
    index: int
    file: str
    xform: str
    visible: bool | None
    collision_model: int | None
    col_box: tuple[float, float, float] | None
    lod_in: float | None
    lod_out: float | None
    clone_count: int | None


@dataclass
class BtbMaterial:
    name: str
    effect: str | None
    technique: str | None
    diffuse: str | None


@dataclass
class BtbTrack:
    folder: Path
    name: str = ""
    physics: str | None = None
    author: str | None = None
    version: str | None = None
    date: str | None = None
    comment: str | None = None
    length_km: float | None = None
    country: str | None = None
    splashscreen: str | None = None
    driveline: list[DrivelinePoint] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    spawn: tuple[float, float, float] | None = None
    spawn_angle_z: float = 0.0
    sun_direction: tuple[float, float, float] | None = None
    map_rows: list[list[list[int]]] = field(default_factory=list)   # [map][row 0..15][cell 0..15]
    map_textures: list[str | None] = field(default_factory=list)
    materials: dict[str, BtbMaterial] = field(default_factory=dict)
    objects: list[BtbObject] = field(default_factory=list)
    objects_zip: zipfile.ZipFile | None = None
    textures: list[str] = field(default_factory=list)

    # -- zip access ---------------------------------------------------------
    def _zip_member(self, stem_ext: str) -> bytes | None:
        z = self.objects_zip
        if z is None:
            return None
        want = stem_ext.replace("\\", "/").lower()
        for info in z.infolist():
            n = info.filename.replace("\\", "/")
            if n.lower() == want or n.lower().endswith("/" + want):
                return z.read(info)
        return None

    def mesh(self, name: str) -> xfile.Mesh | None:
        data = self._zip_member(f"Objects/{name}.x") or self._zip_member(f"{name}.x")
        return xfile.parse(data, f"{name}.x") if data is not None else None

    def placements(self, name: str) -> list[tuple[float, ...]] | None:
        """Matrices of an object's `.xform` (None when the object has no xform: one identity clone)."""
        if not name:
            return None
        data = self._zip_member(f"Objects/{name}.xform") or self._zip_member(f"{name}.xform")
        return xform.parse(data, f"{name}.xform") if data is not None else None

    def map_index_for_texture(self, diffuse_base: str | None) -> int:
        """First `mat.ini` map whose `texture=` base name matches (case-insensitive); 0 if none."""
        if not diffuse_base:
            return 0
        want = diffuse_base.lower()
        for i, t in enumerate(self.map_textures):
            if t and _base(t).lower() == want:
                return i
        return 0


def _base(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _read_ini(folder: Path, name: str, required: bool = True) -> list[ini.Section]:
    p = in_dir(folder, name)
    if p is None:
        if required:
            raise Rbr2StjError(f"{folder.name}: {name} not found (is this a BTB stage folder?)")
        return []
    return ini.parse(guard.read_file(p))


def is_btb_folder(folder: Path) -> bool:
    return folder.is_dir() and in_dir(folder, "track.ini") is not None and in_dir(folder, "driveline.ini") is not None


def _int(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v.strip())
    except ValueError:
        return None


def parse_notes(sections: list[ini.Section]) -> list[Note]:
    head = ini.find_section(sections, "PACENOTES")
    if head is None:
        return []
    count = _int(head.get("count")) or 0
    out = []
    for i in range(count):
        s = ini.find_section(sections, f"P{i}")
        if s is None:
            continue
        tag, flag = _int(s.get("type")), _int(s.get("flag"))
        dist = ini.scan_float(s.get("distance") or "")
        if tag is None or dist is None:
            continue
        out.append(Note(tag, dist, flag or 0))
    return out


def parse_driveline(sections: list[ini.Section]) -> list[DrivelinePoint]:
    sec = ini.find_section(sections, "DRIVELINE")
    if sec is None:
        raise FormatError("driveline.ini: no [DRIVELINE] section")
    count = _int(sec.get("count"))
    if count is None:
        raise FormatError("driveline.ini: bad count")
    pts = []
    for i in range(count):
        v = sec.get(f"K{i}")
        if v is None:
            raise FormatError(f"driveline.ini: no K{i}")
        nums = []
        for t in v.split(","):
            try:
                nums.append(float(t.strip()))
            except ValueError:
                nums.append(0.0)
        if len(nums) != 8:
            raise FormatError(f"driveline.ini: K{i} has {len(nums)} values, expected 8")
        pts.append(DrivelinePoint(tuple(nums[0:3]), tuple(nums[3:6]), nums[6]))
    return pts


def open_folder(folder: str | Path) -> BtbTrack:
    f = Path(folder)
    if not f.is_dir():
        raise Rbr2StjError(f"{f}: not a folder")
    if not is_btb_folder(f):
        raise Rbr2StjError(f"{f}: not a BTB stage folder (needs track.ini and driveline.ini)")
    t = BtbTrack(folder=f)

    info = ini.find_section(_read_ini(f, "track.ini"), "INFO")
    if info is not None:
        g = lambda k: ini.unquote(info.get(k)) or None  # noqa: E731
        t.name = g("name") or f.name
        t.physics = (g("physics") or "").lower() or None
        t.author, t.version, t.date, t.comment = g("author"), g("version"), g("date"), g("comment")
        t.splashscreen = g("splashscreen")
        t.country = g("countrycode")
        km = g("length")
        try:
            t.length_km = float(km) if km else None
        except ValueError:
            t.length_km = None
    t.name = t.name or f.name

    t.driveline = parse_driveline(_read_ini(f, "driveline.ini"))
    t.notes = parse_notes(_read_ini(f, "pacenotes.ini", required=False))

    sp = ini.find_section(_read_ini(f, "spawn.ini", required=False), "SPAWN")
    if sp is not None:
        try:
            t.spawn = (float(sp.get("m12") or 0), float(sp.get("m13") or 0), float(sp.get("m14") or 0))
            t.spawn_angle_z = float(sp.get("angleZ") or 0)
        except ValueError:
            t.spawn = None
    st = ini.find_section(_read_ini(f, "settings.ini", required=False), "SETTINGS")
    if st is not None and st.get("sunDirection"):
        vals = [x for x in (ini.scan_float(p) for p in st.get("sunDirection").split(",")) if x is not None]
        if len(vals) == 3:
            t.sun_direction = (vals[0], vals[1], vals[2])

    mat = _read_ini(f, "mat.ini", required=False)
    head = ini.find_section(mat, "MAPS")
    if head is not None:
        for i in range(_int(head.get("count")) or 0):
            m = ini.find_section(mat, f"MAP{i}")
            rows = [ini.data_cells(m.get(f"data{r}") or "") if m else [0] * 16 for r in range(16)]
            t.map_rows.append(rows)
            t.map_textures.append(ini.unquote(m.get("texture")) if m else None)

    mats = _read_ini(f, "materials.ini", required=False)
    head = ini.find_section(mats, "MATERIALS")
    if head is not None:
        for i in range(_int(head.get("count")) or 0):
            val = head.get(f"MAT_{i}")
            if not val:
                continue
            section = val.split("-blend")[0].strip()
            s = ini.find_section(mats, section)
            t.materials[section.lower()] = BtbMaterial(
                section, s.get("effectName") if s else None, s.get("techniqueName") if s else None,
                ini.unquote(s.get("diffuseTexture")) if s else None)
        # .x files name a material by its materials.ini section even when no MAT_<i> lists it
        for s in mats:
            if s.name.lower() not in t.materials and s.get("diffuseTexture") is not None:
                t.materials[s.name.lower()] = BtbMaterial(s.name, s.get("effectName"), s.get("techniqueName"),
                                                          ini.unquote(s.get("diffuseTexture")))

    ol = _read_ini(f, "objectlist.ini", required=False)
    head = ini.find_section(ol, "OBJECTS")
    if head is not None:
        for i in range(_int(head.get("count")) or 0):
            s = ini.find_section(ol, f"Object_{i}")
            if s is None:
                continue
            vis = _int(s.get("Visible"))
            t.objects.append(BtbObject(
                i, ini.unquote(s.get("file")) or "", ini.unquote(s.get("xform")) or "",
                None if vis is None else bool(vis), _int(s.get("CollisionModel")),
                ini.scan_col_box(s.get("colBox")) if s.get("colBox") else None,
                ini.scan_float(s.get("LODIn") or ""), ini.scan_float(s.get("LODOut") or ""), _int(s.get("cloneCount"))))

    oz = in_dir(f, "Objects.zip")
    if oz is not None:
        try:
            t.objects_zip = zipfile.ZipFile(oz)
        except zipfile.BadZipFile:
            raise FormatError(f"{oz.name}: not a valid zip archive") from None
    tz = in_dir(f, "Textures.zip")
    if tz is not None:
        try:
            with zipfile.ZipFile(tz) as z:
                t.textures = [i.filename for i in z.infolist() if not i.is_dir()]
        except zipfile.BadZipFile:
            pass
    return t
