"""`.lbs` visual geometry file: car start (2,11) and animation objects (2,14).

Format: reRBR notes/formats.md "Stage geometry" and crates/rbr-formats/src/lbs/{mod,read}.rs (read, not copied).
Frame: Y up, left-handed (the `.trk` frame with y and z swapped). Only the chunks rbr2stj uses are decoded:

  (2,11): f32 matrix[16] (zero in RSF stages), Vec3 position, Vec3 rotation (axis-angle; BTB: (0, yaw, 0))
  (2,14): u32 count; per item: cstr name, u32 id, flags, group, cstr script A, cstr script B, 5 * Vec3, u32 colour

Visual geometry (1,0) (P6): u32 block count, u32 sky word, then per block 9 views {u32 index count (0: an empty view,
followed by one more u32), u16 indices, u32 vertex count, vertices of the slot's stride}, u32 render-chunk count,
0x70-byte render chunks, bounds (6 f32). Slot strides [0x10, 0x18, 0x30, 0x24, 0x3c, 0x20, 0x38, 0x2c, 0x44]; vertex
layouts known for slots 0, 5, 6, 7, 8 (position f32x3 at 0; colour D3DCOLOR BGRA at 12 (slots 0, 5, 7) or 24 (6, 8);
uv0 at 16 (5, 7) or 28 (6, 8)). Render chunk: word 0 view slot, 0x0c start index, 0x10 triangle count, 0x44 texture
count, 0x48/0x4c texture indices (`Texture<N>` of the stage .ini), byte 0x6c draw list (1 near, 2 always, 3 far).
Indices are absolute in the view's buffer; a chunk's triangles are a plain list from `start index`.
Sources: notes/formats.md "Stage geometry" and crates/rbr-formats/src/lbs/read.rs, rbr-render/src/stage/mesh.rs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError
from .chunks import read_chunks

START = (2, 11)
ANIMATION_OBJECTS = (2, 14)
GEOMETRY = (1, 0)
VIEW_STRIDES = (0x10, 0x18, 0x30, 0x24, 0x3C, 0x20, 0x38, 0x2C, 0x44)
RENDER_CHUNK_LEN = 0x70
# slot -> (colour offset, uv0 offset or None) for the layouts known from the renderer
SLOT_LAYOUT = {0: (12, None), 5: (12, 16), 7: (12, 16), 6: (24, 28), 8: (24, 28)}


@dataclass
class StartLocation:
    matrix: tuple[float, ...]
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]


@dataclass
class AnimationObject:
    name: str
    ids: tuple[int, int, int]
    scripts: tuple[str, str]
    vectors: list[tuple[float, float, float]]
    colour: int


@dataclass
class RenderChunk:
    slot: int
    start_index: int
    triangles: int
    textures: list[int]
    draw_list: int


@dataclass
class View:
    indices: list[int]
    positions: list[tuple[float, float, float]]
    colours: list[int | None]                 # D3DCOLOR (BGRA) words
    uvs: list[tuple[float, float] | None]


@dataclass
class GeometryBlock:
    views: list[View | None]
    chunks: list[RenderChunk]


@dataclass
class LbsFile:
    geometry: list[GeometryBlock] = field(default_factory=list)
    skipped_chunks: int = 0                   # chunks whose view slot has no known layout
    start: StartLocation | None = None
    animation_objects: list[AnimationObject] = field(default_factory=list)
    chunk_kinds: list[tuple[int, int]] = field(default_factory=list)


def parse(data: bytes) -> LbsFile:
    out = LbsFile()
    for ch in read_chunks(data, ".lbs"):
        if len(ch.header) != 8:
            raise FormatError(f".lbs: chunk header of {len(ch.header)} bytes, expected 8")
        k = ch.kind
        out.chunk_kinds.append(k)
        if k == GEOMETRY:
            _geometry(ch.data, out)
        elif k == START:
            r = Reader(ch.data, label=".lbs start")
            out.start = StartLocation(r.f32s(16), r.vec3(), r.vec3())
        elif k == ANIMATION_OBJECTS:
            r = Reader(ch.data, label=".lbs animation objects")
            for _ in range(r.u32()):
                name = lossy(r.cstr())
                ids = r.u32s(3)
                a, b = lossy(r.cstr()), lossy(r.cstr())
                vecs = [r.vec3() for _ in range(5)]
                out.animation_objects.append(AnimationObject(name, ids, (a, b), vecs, r.u32()))
    return out


def _geometry(payload: bytes, out: LbsFile):
    r = Reader(payload, label=".lbs geometry")
    nblocks = r.u32()
    if nblocks == 0 and r.eof():
        return                                 # the BTB plugin's 4-byte stub: no geometry
    r.u32()                                    # sky word
    for _ in range(nblocks):
        views: list[View | None] = []
        for slot in range(9):
            nidx = r.u32()
            if nidx == 0:
                r.u32()
                views.append(None)
                continue
            if nidx & 0x80000000:
                raise FormatError(".lbs geometry: a view's index count has bit 31 set")
            r.need(nidx * 2)
            indices = list(struct.unpack_from(f"<{nidx}H", r.take(nidx * 2)))
            nv = r.u32()
            stride = VIEW_STRIDES[slot]
            if nv & 0xF0000000:
                raise FormatError(".lbs geometry: a view's vertex count has flag bits set")
            r.need(nv * stride)
            raw = r.take(nv * stride)
            lay = SLOT_LAYOUT.get(slot)
            if lay is None:
                views.append(None)             # layout unknown: the chunks that use it are skipped and counted
                continue
            pos, col, uv = [], [], []
            for v in range(nv):
                o = v * stride
                pos.append(struct.unpack_from("<3f", raw, o))
                col.append(struct.unpack_from("<I", raw, o + lay[0])[0])
                uv.append(struct.unpack_from("<2f", raw, o + lay[1]) if lay[1] is not None else None)
            views.append(View(indices, pos, col, uv))
        nchunks = r.u32()
        chunks = []
        for _ in range(nchunks):
            rec = r.take(RENDER_CHUNK_LEN)
            slot, = struct.unpack_from("<I", rec, 0)
            if slot >= 9:
                raise FormatError(f".lbs geometry: a render chunk names view slot {slot}")
            start, prims = struct.unpack_from("<II", rec, 0x0C)
            ntex, = struct.unpack_from("<I", rec, 0x44)
            if ntex > 2:
                raise FormatError(".lbs geometry: a render chunk has more than two textures")
            texs = list(struct.unpack_from(f"<{ntex}I", rec, 0x48)) if ntex else []
            if views[slot] is None:
                out.skipped_chunks += 1
                continue
            chunks.append(RenderChunk(slot, start, prims, texs, rec[0x6C]))
        r.skip(24)                             # bounds
        out.geometry.append(GeometryBlock(views, chunks))
