"""`.col` stage collision (`OC7R`).

Format: reRBR notes/formats.md "Collision" and the layout comments of crates/rbr-formats/src/col/mod.rs
(read, not copied). Little-endian. Z up (same frame as `.trk`).

  file header 16 bytes: u32 'OC7R' (0x5237434f), u32 root offset, u32 cell count, u32 cell table offset
  root (0x28 bytes): u8 4, u8 3, u16 slot, u32 flags, u32 vertex count (0), u32 vertex rel, u32 tree rel,
        u32 brake-wall rel (0 = none), u32 list A count, u32 list A rel, u32 list B count, u32 list B rel
        (relative offsets are from the root). Lists A and B are 0x40-byte records of four Vec4 corners:
        A = wet puddle quads, B = deep water quads.
  cell: u8 sub, u8 1, u16 slot, u32 graft word (top byte depth, low bits the path, LSB first), u32 vertex count,
        u32 vertex rel (0x14), u32 tree rel; Vec3 vertices; tree
  tree node (0x20): f32 bounds[6] (min, extent), u32 flags (bits 0-20 triangle count, bits 21-31 placeholder/slot),
        u32 pointer relative to the tree root. Count 0 with a pointer: a pair of child nodes. Count > 0: leaf.
  triangle (18 bytes): u16 v[3]; u16 attr A, u16 attr B (3 x 5-bit per-vertex values, bit 15 a flag);
        u8 material 0, u8 material 1 (`.mat` run indices); u8 nib0[3], u8 nib1[3] (low nibble = `.mat` cell coord).
        Attr A / 31 interpolated = the tyre's blend weight from surface 0 towards surface 1.
  graft: each cell's root node overwrites the root placeholder its path reaches; a later cell wins.
  brake wall (at the wall rel): u32 size, u32 n, u32 points rel, u32 root rel (all from the wall start); n (x, y)
        points as (inner, outer) pairs; tree of 0x18-byte nodes {f32 cx, cy, hx, hy, u32 item count, u32 pointer};
        a leaf's pointer leads to u16 items: bits 0-13 index, bit 14 sensor only, bit 15 respawn.
        INFERRED (no retail file at hand): an item index k names the segment between point pairs k and k+1.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binio import u32_at
from ..errors import FormatError

MAGIC = 0x5237434F
NODE = 0x20
PLACEHOLDER_MASK = 0xFFE00000
COUNT_MASK = 0x001FFFFF


@dataclass(slots=True)
class Triangle:
    v: tuple[int, int, int]
    attr_a: tuple[int, int, int]
    attr_b: tuple[int, int, int]
    flag_a: bool
    flag_b: bool
    mat: tuple[int, int]
    nib: tuple[tuple[int, int, int], tuple[int, int, int]]


@dataclass
class Cell:
    graft: int
    vertices: list[tuple[float, float, float]]
    triangles: list[Triangle]


@dataclass
class WallItem:
    index: int
    sensor: bool
    respawn: bool


@dataclass
class BrakeWall:
    points: list[tuple[float, float]]            # (inner, outer) pairs: point 2k inner, 2k+1 outer
    items: list[WallItem]


@dataclass
class ColFile:
    cells: list[Cell] = field(default_factory=list)     # only the cells that survive grafting, in file order
    puddles: list[list[tuple[float, float, float]]] = field(default_factory=list)   # list A quads (4 corners)
    deep_water: list[list[tuple[float, float, float]]] = field(default_factory=list)  # list B quads
    brake_wall: BrakeWall | None = None
    superseded_cells: int = 0                            # cells overwritten by a later cell on the same graft


def _node(data: bytes, off: int):
    if off < 0 or off + NODE > len(data):
        raise FormatError(f".col: tree node at offset {off} is outside the {len(data)}-byte file")
    b = struct.unpack_from("<6f", data, off)
    flags, ptr = struct.unpack_from("<II", data, off + 24)
    return b, flags, ptr


def _walk_tree(data: bytes, base: int, mask: int):
    """Yield (triangle count, triangle array offset) for each leaf reachable from the root at `base`."""
    stack = [base]
    seen = set()
    while stack:
        off = stack.pop()
        if off in seen:
            raise FormatError(f".col: tree node at offset {off} is reached twice")
        seen.add(off)
        _b, flags, ptr = _node(data, off)
        if flags & mask:
            continue  # graft placeholder
        count = flags & COUNT_MASK
        if count == 0:
            if ptr:
                stack.append(base + ptr + NODE)
                stack.append(base + ptr)
        else:
            yield count, base + ptr


def _triangles(data: bytes, base: int, nverts: int, label: str) -> list[Triangle]:
    tris = []
    for count, off in _walk_tree(data, base, 0):
        if off + count * 18 > len(data):
            raise FormatError(f".col: {label}: triangle array at offset {off} runs past the end of the file")
        for i in range(count):
            q = off + i * 18
            v = struct.unpack_from("<3H", data, q)
            if max(v) >= nverts:
                raise FormatError(f".col: {label}: vertex index {max(v)} is outside the cell's {nverts} vertices")
            aa, ab = struct.unpack_from("<2H", data, q + 6)
            m0, m1 = data[q + 10], data[q + 11]
            g = data[q + 12:q + 18]
            tris.append(Triangle(
                v,
                (aa & 31, (aa >> 5) & 31, (aa >> 10) & 31),
                (ab & 31, (ab >> 5) & 31, (ab >> 10) & 31),
                bool(aa & 0x8000), bool(ab & 0x8000), (m0, m1),
                ((g[0] & 15, g[1] & 15, g[2] & 15), (g[3] & 15, g[4] & 15, g[5] & 15))))
    return tris


def _resolve_graft(data: bytes, root_base: int, word: int) -> int | None:
    """Offset of the root placeholder node a cell's graft word reaches, or None if the path does not end on one."""
    depth = word >> 24
    if depth == 0:
        return None
    off, bits = root_base, word
    for _ in range(depth):
        _b, flags, ptr = _node(data, off)
        if flags & PLACEHOLDER_MASK or flags & COUNT_MASK or not ptr:
            return None
        off = root_base + ptr + (bits & 1) * NODE
        bits >>= 1
    _b, flags, _p = _node(data, off)
    return off if flags & PLACEHOLDER_MASK else None


def _quads(data: bytes, off: int, count: int) -> list[list[tuple[float, float, float]]]:
    if count == 0:
        return []
    if off + count * 64 > len(data):
        raise FormatError(".col: a water quad list runs past the end of the file")
    out = []
    for i in range(count):
        f = struct.unpack_from("<16f", data, off + i * 64)
        out.append([(f[0], f[1], f[2]), (f[4], f[5], f[6]), (f[8], f[9], f[10]), (f[12], f[13], f[14])])
    return out


def _brake_wall(data: bytes, off: int) -> BrakeWall | None:
    if off + 16 > len(data):
        raise FormatError(".col: brake-wall header runs past the end of the file")
    _size, n, prel, rrel = struct.unpack_from("<4I", data, off)
    if n == 0:
        return None
    poff = off + prel
    if poff + n * 8 > len(data):
        raise FormatError(".col: brake-wall points run past the end of the file")
    pts = [struct.unpack_from("<2f", data, poff + 8 * i) for i in range(n)]
    items: dict[int, WallItem] = {}
    stack = [off + rrel]
    seen = set()
    while stack:
        noff = stack.pop()
        if noff in seen or noff + 0x18 > len(data):
            raise FormatError(".col: brake-wall tree node is outside the file or shared")
        seen.add(noff)
        _cx, _cy, _hx, _hy, nitems, ptr = struct.unpack_from("<4fII", data, noff)
        if nitems:
            ioff = off + ptr
            if ioff + 2 * nitems > len(data):
                raise FormatError(".col: brake-wall leaf items run past the end of the file")
            for u in struct.unpack_from(f"<{nitems}H", data, ioff):
                idx = u & 0x3FFF
                prev = items.get(idx)
                items[idx] = WallItem(idx, bool(u & 0x4000) or (prev.sensor if prev else False),
                                      bool(u & 0x8000) or (prev.respawn if prev else False))
        elif ptr:
            stack.append(off + ptr + 0x18)
            stack.append(off + ptr)
    return BrakeWall(pts, [items[k] for k in sorted(items)])


def parse(data: bytes) -> ColFile:
    if len(data) < 16:
        raise FormatError(f".col: {len(data)} bytes is shorter than the 16-byte header")
    magic, root_off, ncells, table_off = struct.unpack_from("<4I", data, 0)
    if magic != MAGIC:
        raise FormatError(f".col: bad magic {magic:#010x}, expected 'OC7R'")
    if table_off + ncells * 4 > len(data) or root_off + 0x28 > len(data):
        raise FormatError(".col: header offsets run past the end of the file")
    if data[root_off] != 4 or data[root_off + 1] != 3:
        raise FormatError(f".col: the root object at {root_off} is not type 4/3")
    tree_rel = u32_at(data, root_off + 0x10)
    wall_rel = u32_at(data, root_off + 0x14)
    a_count, a_rel = u32_at(data, root_off + 0x18), u32_at(data, root_off + 0x1C)
    b_count, b_rel = u32_at(data, root_off + 0x20), u32_at(data, root_off + 0x24)
    root_base = root_off + tree_rel

    out = ColFile(puddles=_quads(data, root_off + a_rel, a_count), deep_water=_quads(data, root_off + b_rel, b_count))
    if wall_rel:
        out.brake_wall = _brake_wall(data, root_off + wall_rel)

    grafted: dict[int, int] = {}   # placeholder offset -> index into `cells`
    cells: list[Cell] = []
    for i in range(ncells):
        off = u32_at(data, table_off + 4 * i)
        if off + 0x14 > len(data):
            raise FormatError(f".col: cell {i} header at offset {off} is outside the file")
        if data[off + 1] != 1:
            raise FormatError(f".col: cell {i} at {off} has mode {data[off + 1]}, expected 1")
        word, nv, vrel, trel = struct.unpack_from("<4I", data, off + 4)
        if nv == 0:
            continue  # the loader skips cells without vertices
        if off + vrel + nv * 12 > len(data):
            raise FormatError(f".col: cell {i} vertices run past the end of the file")
        verts = [struct.unpack_from("<3f", data, off + vrel + 12 * k) for k in range(nv)]
        tris = _triangles(data, off + trel, nv, f"cell {i}")
        target = _resolve_graft(data, root_base, word)
        if target is None:
            raise FormatError(f".col: cell {i}: graft path {word:#x} does not reach a placeholder")
        if target in grafted:
            out.superseded_cells += 1  # later cell wins (community stages)
        grafted[target] = len(cells)
        cells.append(Cell(word, verts, tris))
    out.cells = [cells[i] for i in sorted(set(grafted.values()))]
    return out
