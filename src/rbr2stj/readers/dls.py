"""`.dls` AnimData: the `Driveline` animation set holds start/finish/split/end keys, pace notes and triggers.

Format: reRBR notes/formats.md "Stage files" (`.dls`) and "Markers"; layout comments of
crates/rbr-formats/src/{dls,markers}.rs (read, not copied).

  header  56 bytes: "MINAATAD", u32 version (18), u32 minor, 10 * u32 table offsets
          [0 animation sets, 1 splines, 2 triggers, 3 cameras, 4 emitters, 5 helicams, 6 sound spheres,
           7 name block, 8 world vector (0xffffffff = none), 9 named triggers]
  table 0 u32 n; n * {u32 record offset, u32 companion}
          record = 17 words: w0 name offset (in the name block), w1..w7 counts, w9..w15 offsets, w16 replay stream
          arrays (count word, offset word, words per row): trigger data (1, 9, 2 = {object id, kind}),
          plain triggers (2, 10, 3), animation channels (3, 11, 3 = {trigger data index, f32 distance, f32 lead}),
          (5, 13, 4), keys (6, 14, 3 = {u32 tag, u32 flags, f32 distance}), (7, 15, 5)
  table 6 u32 n; n * Vec4 (sound spheres: xyz + radius value)
  table 7 u32 length; bytes (NUL-terminated names)
  table 9 u32 n; n * {u32 object id, u32 name offset}

The reader is tolerant: tables it does not need may be missing (offset out of range or 0xffffffff).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError

MAGIC = b"MINAATAD"
HEADER_LEN = 56
SET_NAME = "driveline"

TAG_START, TAG_FINISH, TAG_SPLIT, TAG_END = 0x15, 0x16, 0x17, 0x18


@dataclass
class Key:
    tag: int
    flags: int
    distance: float


@dataclass
class Trigger:
    """An animation channel: character `object` fires when `distance - lead * speed < x`."""
    object: int
    kind: int
    distance: float
    lead: float


@dataclass
class PlainTrigger:
    object: int
    kind: int
    distance: float
    flags: int


@dataclass
class Dls:
    version: int = 0
    keys: list[Key] = field(default_factory=list)          # of the Driveline set, sorted by distance
    triggers: list[Trigger] = field(default_factory=list)
    plain_triggers: list[PlainTrigger] = field(default_factory=list)
    named_triggers: list[tuple[str, int]] = field(default_factory=list)
    spheres: list[tuple[float, float, float, float]] = field(default_factory=list)
    set_names: list[str] = field(default_factory=list)


def _table(data: bytes, offsets: list[int], i: int) -> int | None:
    v = offsets[i]
    return None if v == 0xFFFFFFFF or v >= len(data) else v


def _name(strings: bytes, off: int) -> str:
    if off >= len(strings):
        raise FormatError(f".dls: name offset {off} is outside the {len(strings)}-byte name block")
    end = strings.find(b"\0", off)
    return lossy(strings[off:end if end >= 0 else len(strings)])


def parse(data: bytes) -> Dls:
    if len(data) < HEADER_LEN:
        raise FormatError(f".dls: {len(data)} bytes is shorter than the {HEADER_LEN}-byte header")
    if data[:8] != MAGIC:
        raise FormatError(".dls: bad AnimData magic; expected MINAATAD")
    version, _minor = struct.unpack_from("<II", data, 8)
    offsets = list(struct.unpack_from("<10I", data, 16))
    out = Dls(version=version)

    strings = b""
    t7 = _table(data, offsets, 7)
    if t7 is not None:
        r = Reader(data, t7, label=".dls names")
        strings = r.take(r.u32())

    t6 = _table(data, offsets, 6)
    if t6 is not None:
        r = Reader(data, t6, label=".dls spheres")
        n = r.u32()
        r.need(n * 16)
        out.spheres = [r.f32s(4) for _ in range(n)]

    t9 = _table(data, offsets, 9)
    if t9 is not None:
        r = Reader(data, t9, label=".dls named triggers")
        n = r.u32()
        r.need(n * 8)
        for _ in range(n):
            obj, noff = r.u32(), r.u32()
            if noff < len(strings):
                out.named_triggers.append((_name(strings, noff), obj))

    t0 = _table(data, offsets, 0)
    if t0 is None:
        raise FormatError(".dls: no animation set table")
    r = Reader(data, t0, label=".dls animation sets")
    n = r.u32()
    r.need(n * 8)
    pairs = [(r.u32(), r.u32()) for _ in range(n)]
    for rec_off, _comp in pairs:
        rr = Reader(data, rec_off, label=".dls animation record")
        w = rr.u32s(17)
        name = _name(strings, w[0]) if w[0] < len(strings) else ""
        out.set_names.append(name)
        if name.lower() != SET_NAME:
            continue

        def array(count_i: int, off_i: int, words: int) -> list[tuple[int, ...]]:
            cnt = w[count_i]
            if cnt == 0:
                return []
            ar = Reader(data, w[off_i], label=f".dls array {count_i}")
            ar.need(cnt * words * 4)
            return [ar.u32s(words) for _ in range(cnt)]

        tdata = array(1, 9, 2)
        keys = array(6, 14, 3)
        out.keys = sorted((Key(a, b, struct.unpack("<f", struct.pack("<I", c))[0]) for a, b, c in keys),
                          key=lambda k: k.distance)
        fl = lambda u: struct.unpack("<f", struct.pack("<I", u))[0]  # noqa: E731
        for idx, dist, lead in array(3, 11, 3):
            if idx < len(tdata):
                out.triggers.append(Trigger(tdata[idx][0], tdata[idx][1], fl(dist), fl(lead)))
        pl = []
        for idx, dist, flags in array(2, 10, 3):
            if idx < len(tdata):
                pl.append(PlainTrigger(tdata[idx][0], tdata[idx][1], fl(dist), flags))
        out.plain_triggers = sorted(pl, key=lambda t: t.distance)
        break
    else:
        raise FormatError(".dls: no animation set named 'Driveline'")
    return out


@dataclass
class Markers:
    """Start, splits, finish, end and pace-note keys of a stage (reRBR notes/formats.md "Markers")."""
    start: float | None
    finish: float | None
    end: float | None
    splits: list[float]
    notes: list[Key]


def markers(dls: Dls) -> Markers:
    """The loader keeps the last key of a tag in sorted order (start/finish/end); at most 16 splits."""
    def last(tag: int) -> float | None:
        for k in reversed(dls.keys):
            if k.tag == tag:
                return k.distance
        return None

    splits = [k.distance for k in dls.keys if k.tag == TAG_SPLIT][:16]
    notes = [k for k in dls.keys if not (TAG_START <= k.tag <= TAG_END)]
    return Markers(last(TAG_START), last(TAG_FINISH), last(TAG_END), splits, notes)
