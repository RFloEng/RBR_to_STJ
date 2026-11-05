"""`.trk` physics file: driveline (3,0x14) and interactive collision meshes (3,0x16).

Format: reRBR notes/formats.md "Stage files" (`.trk`), cross-checked against the layout comments of
crates/rbr-formats/src/trk.rs (read, not copied).

  (3,0x14): u32 count; count * {Vec3 position, Vec3 tangent, f32 distance, u16, u16}      (z up, 32 bytes each)
  (3,0x16): u32 mesh count; per mesh: cstr name, u32 kind, u8 material (surface id), u8 dynamic flag,
            u32 shape (0 none, 1 box = 2 Vec3, 2 sphere = Vec3 + f32), u32 vertex count (low 16 bits used),
            Vec3 vertices, u32 face count (low 16 bits used), faces {u8 tag, 3 * u32 index},
            u32 placement count, placements {u32 key, Vec3 position, Vec3 scale, quat x y z w}  (44 bytes)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError
from .chunks import read_chunks

DRIVELINE = (3, 0x14)
MESHES = (3, 0x16)

# object kinds of (3,0x16) meshes (notes/formats.md `.trk`)
KIND_NAMES = {0: "static", 1: "cone", 2: "block", 3: "sign", 6: "banner", 7: "log", 8: "log_pole",
              9: "fence_bar", 10: "fence_pole"}


@dataclass
class DrivelinePoint:
    position: tuple[float, float, float]
    tangent: tuple[float, float, float]
    distance: float
    words: tuple[int, int] = (0, 0)


@dataclass
class Placement:
    key: int
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    quat: tuple[float, float, float, float]  # x, y, z, w


@dataclass
class CollisionMesh:
    name: str
    kind: int
    material: int
    dynamic: int
    shape: tuple | None  # ("box", min, max) | ("sphere", centre, radius) | None
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int, int]]  # (tag, i, j, k)
    placements: list[Placement]


@dataclass
class TrkFile:
    driveline: list[DrivelinePoint] = field(default_factory=list)
    meshes: list[CollisionMesh] = field(default_factory=list)
    unknown_chunks: list[tuple[int, int]] = field(default_factory=list)


def parse_driveline(payload: bytes) -> list[DrivelinePoint]:
    r = Reader(payload, label=".trk driveline")
    n = r.u32()
    r.need(n * 32)
    pts = []
    for _ in range(n):
        pos = r.vec3()
        tan = r.vec3()
        d = r.f32()
        a, b = r.u16(), r.u16()
        pts.append(DrivelinePoint(pos, tan, d, (a, b)))
    return pts


def parse_meshes(payload: bytes) -> list[CollisionMesh]:
    r = Reader(payload, label=".trk meshes")
    count = r.u32()
    meshes = []
    for _ in range(count):
        name = lossy(r.cstr())
        kind = r.u32()
        material = r.u8()
        dynamic = r.u8()
        shape_kind = r.u32()
        shape: tuple | None = None
        if shape_kind == 1:
            shape = ("box", r.vec3(), r.vec3())
        elif shape_kind == 2:
            shape = ("sphere", r.vec3(), r.f32())
        nv = r.u32() & 0xFFFF
        r.need(nv * 12)
        verts = [r.vec3() for _ in range(nv)]
        nf = r.u32() & 0xFFFF
        r.need(nf * 13)
        faces = []
        for _ in range(nf):
            tag = r.u8()
            i, j, k = r.u32s(3)
            faces.append((tag, i & 0xFF, j & 0xFF, k & 0xFF))  # the loader keeps each word's low byte
        npl = r.u32()
        r.need(npl * 44)
        pls = []
        for _ in range(npl):
            key = r.u32()
            pos = r.vec3()
            scale = r.vec3()
            q = r.f32s(4)
            pls.append(Placement(key, pos, scale, q))
        meshes.append(CollisionMesh(name, kind, material, dynamic, shape, verts, faces, pls))
    return meshes


def parse(data: bytes) -> TrkFile:
    out = TrkFile()
    for ch in read_chunks(data, ".trk"):
        k = ch.kind
        if k == DRIVELINE:
            out.driveline = parse_driveline(ch.data)
        elif k == MESHES:
            out.meshes = parse_meshes(ch.data)
        else:
            out.unknown_chunks.append(k if k else (-1, -1))
    if not out.driveline:
        raise FormatError(".trk: no driveline chunk (3,0x14) or it is empty")
    return out
