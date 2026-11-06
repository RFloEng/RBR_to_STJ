"""The other visual chunks of an `.lbs`: object lanes (1,1), clouds (1,2), sky dome (1,3), interactive objects (1,6),
reflection (1,7) and water (1,8) faces.

Layouts: reRBR notes/formats.md "Stage geometry" and crates/rbr-formats/src/lbs/read.rs (read, not copied). All use
the geometry frame of `lbs.py` (Y up, left-handed). Every item ends in a u16 index list and a vertex buffer whose
first 12 bytes are a position; where the stride allows, a D3DCOLOR sits at 12 and a UV pair at 16 (checked against the
retail cloud domes: stride 24 = position, colour, uv, one more word). The layout beyond that is not documented, so the
raw stride is kept and the extra bytes are not interpreted.

  lane (1,1): u32 lane count; per lane u32 flag, and if set two runs of {u32 count, items}
    lane item: u32 flags, u32 kind, [flags&0x10: u32 texture 0], [flags&0x20: u32 texture 1], u32 has-box, u32 stride,
      u32 unknown, u32 index count, u16 indices, u8 second-index mode (0 none, 1 same, 2 own: u32 n + u16s),
      u32 vertex count, [box: 6 f32, and with flags&0x10 four more words], vertices, 24 bytes of bounds
  sky / clouds (1,2) (1,3): cstr name, u32 count, items {[dome: Vec3 centre], u32 flags, u32 kind, [0x10: u32 tex 0],
      [0x20: u32 tex 1], u32 vertex size, u32 unknown, u32 index count, u16 indices, u32 vertex count, vertices}
  interactive (1,6): u32 count; {cstr name, u8, u32 draw count, draws {u32 flags, u32 unknown, [0x10 tex 0], [0x20 tex 1],
      u32 vertex size, u32 stride, u32 index count, u16 indices, u32 vertex count, vertices}, u32 n, n * {u32, 16 f32}}
  reflection / water (1,7) (1,8): u32 face count; {cstr name, u8, u32 item count, items {u32 flags, u32 key0,
      [0x10, 0x20, 0x40: u32 key, with flags&0x80 two f32], u32 vertex size, u32 unknown, u32 index count, u16 indices,
      u32 vertex count, vertices}}
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError

LANES, CLOUDS, SKY_DOME, INTERACTIVE, REFLECTION, WATER = (1, 1), (1, 2), (1, 3), (1, 6), (1, 7), (1, 8)
KINDS = (LANES, CLOUDS, SKY_DOME, INTERACTIVE, REFLECTION, WATER)


@dataclass
class Item:
    """One drawable: triangles over a vertex buffer, in the geometry frame."""
    group: str                                   # lanes | clouds | sky | interactive | reflection | water
    name: str = ""                               # face / object name where the chunk has one
    block: int = 0                               # lane index (lanes), else 0
    run: int = 0                                 # lane run 0/1
    flags: int = 0
    kind: int = 0
    textures: list[int] = field(default_factory=list)     # `Texture<N>` indices (of the file's own ini)
    stride: int = 0
    indices: list[int] = field(default_factory=list)
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    colours: list[int | None] = field(default_factory=list)
    uvs: list[tuple[float, float] | None] = field(default_factory=list)
    centre: tuple[float, float, float] | None = None
    extra: dict = field(default_factory=dict)    # keys, unknown words, boxes, matrices: kept verbatim for x_rbr


@dataclass
class Extras:
    items: list[Item] = field(default_factory=list)
    lane_flags: list[int] = field(default_factory=list)


def _vertices(raw: bytes, n: int, stride: int, item: Item):
    for v in range(n):
        o = v * stride
        item.positions.append(struct.unpack_from("<3f", raw, o))
        item.colours.append(struct.unpack_from("<I", raw, o + 12)[0] if stride >= 16 else None)
        item.uvs.append(struct.unpack_from("<2f", raw, o + 16) if stride >= 24 else None)


def _indices(r: Reader, n: int) -> list[int]:
    if n & 0x80000000:
        raise FormatError(".lbs: an index count has bit 31 set")
    r.need(n * 2)
    return list(struct.unpack_from(f"<{n}H", r.take(n * 2)))


def _buffer(r: Reader, item: Item, stride: int, indices: list[int], nverts: int):
    if nverts & 0xF0000000 or nverts * stride > r.remaining():
        raise FormatError(".lbs: a vertex buffer runs past the end of its chunk")
    item.stride, item.indices = stride, indices
    _vertices(r.take(nverts * stride), nverts, stride, item)


def parse_lanes(data: bytes, out: Extras):
    r = Reader(data, label=".lbs lanes")
    for lane in range(r.u32()):
        flag = r.u32()
        out.lane_flags.append(flag)
        if not flag:
            continue
        for run in range(2):
            for _ in range(r.u32()):
                it = Item("lanes", block=lane, run=run, flags=r.u32(), kind=r.u32())
                if it.flags & 0x10:
                    it.textures.append(r.u32())
                if it.flags & 0x20:
                    it.textures.append(r.u32())
                has_box, stride, it.extra["unknown"] = r.u32(), r.u32(), r.u32()
                idx = _indices(r, r.u32())
                mode = r.u8()
                if mode == 2:
                    it.extra["second_indices"] = _indices(r, r.u32())
                elif mode == 1:
                    it.extra["second_indices"] = "same"
                elif mode != 0:
                    raise FormatError(".lbs: a lane item's index flag is not 0, 1 or 2")
                nv = r.u32()
                if has_box:
                    it.extra["box"] = list(r.f32s(6))
                    if it.flags & 0x10:
                        it.extra["box_words"] = list(r.u32s(4))
                _buffer(r, it, stride, idx, nv)
                it.extra["bounds"] = list(r.f32s(6))
                out.items.append(it)


def parse_sky(data: bytes, out: Extras, dome: bool):
    r = Reader(data, label=".lbs sky")
    name = lossy(r.cstr())
    for _ in range(r.u32()):
        it = Item("sky" if dome else "clouds", name=name)
        if dome:
            it.centre = r.vec3()
        it.flags, it.kind = r.u32(), r.u32()
        if it.flags & 0x10:
            it.textures.append(r.u32())
        if it.flags & 0x20:
            it.textures.append(r.u32())
        stride, it.extra["unknown"] = r.u32(), r.u32()
        idx = _indices(r, r.u32())
        _buffer(r, it, stride, idx, r.u32())
        out.items.append(it)


def parse_interactive(data: bytes, out: Extras):
    r = Reader(data, label=".lbs interactive objects")
    for _ in range(r.u32()):
        name, flag = lossy(r.cstr()), r.u8()
        for _d in range(r.u32()):
            it = Item("interactive", name=name, flags=r.u32())
            it.extra["object_flag"], it.extra["unknown"] = flag, r.u32()
            if it.flags & 0x10:
                it.textures.append(r.u32())
            if it.flags & 0x20:
                it.textures.append(r.u32())
            vsize, it.extra["stride_word"] = r.u32(), r.u32()
            idx = _indices(r, r.u32())
            _buffer(r, it, vsize, idx, r.u32())
            out.items.append(it)
        vis = [{"word": r.u32(), "matrix": list(r.f32s(16))} for _ in range(r.u32())]
        if vis:
            for it in out.items:
                if it.group == "interactive" and it.name == name:
                    it.extra["visibility"] = vis


def parse_surface(data: bytes, out: Extras, group: str):
    r = Reader(data, label=f".lbs {group} faces")
    for _ in range(r.u32()):
        name = lossy(r.cstr())
        r.u8()
        for _i in range(r.u32()):
            it = Item(group, name=name, flags=r.u32())
            it.extra["key0"] = r.u32()
            keys = {}
            for bit, label in ((0x10, "key10"), (0x20, "key20"), (0x40, "key40")):
                if it.flags & bit:
                    k = r.u32()
                    keys[label] = [k] + (list(r.f32s(2)) if it.flags & 0x80 else [])
            it.extra.update(keys)
            vsize, it.extra["unknown"] = r.u32(), r.u32()
            idx = _indices(r, r.u32())
            _buffer(r, it, vsize, idx, r.u32())
            out.items.append(it)


def parse(chunks) -> Extras:
    """`chunks` are `chunks.Chunk` objects (kind, data)."""
    out = Extras()
    for ch in chunks:
        k = ch.kind
        if k == LANES:
            parse_lanes(ch.data, out)
        elif k == CLOUDS:
            parse_sky(ch.data, out, dome=False)
        elif k == SKY_DOME:
            parse_sky(ch.data, out, dome=True)
        elif k == INTERACTIVE:
            parse_interactive(ch.data, out)
        elif k == REFLECTION:
            parse_surface(ch.data, out, "reflection")
        elif k == WATER:
            parse_surface(ch.data, out, "water")
    return out
