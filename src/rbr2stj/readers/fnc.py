"""`.fnc` fences.

Format: reRBR notes/formats.md "Stage files" (`.fnc`) and the layout comment of crates/rbr-formats/src/fnc.rs:

  u32 magic (2); u32 group count; per group: u32 post count, u32 fence type, post type, material, rail material,
  f32 centre[3], f32 half extent[3], posts {f32 position[3], centre[3], half extent[3], u32 BGRA colour} (40 bytes);
  then (only when there is at least one group) u32 texture count, and if non-zero u32 name width and that many
  fixed-width NUL-padded names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError

MAGIC = 2


@dataclass
class Post:
    position: tuple[float, float, float]
    centre: tuple[float, float, float]
    half: tuple[float, float, float]
    colour: int


@dataclass
class Group:
    fence_type: int
    post_type: int
    material: int
    rail_material: int
    centre: tuple[float, float, float]
    half: tuple[float, float, float]
    posts: list[Post]


@dataclass
class FncFile:
    groups: list[Group] = field(default_factory=list)
    textures: list[str] = field(default_factory=list)


def parse(data: bytes) -> FncFile:
    r = Reader(data, label=".fnc")
    magic = r.u32()
    if magic != MAGIC:
        raise FormatError(f".fnc: bad magic {magic}, expected {MAGIC}")
    ngroups = r.u32()
    groups = []
    for _ in range(ngroups):
        npost = r.u32()
        ft, pt, mat, rail = r.u32s(4)
        centre, half = r.vec3(), r.vec3()
        r.need(npost * 40)
        posts = []
        for _ in range(npost):
            posts.append(Post(r.vec3(), r.vec3(), r.vec3(), r.u32()))
        groups.append(Group(ft, pt, mat, rail, centre, half, posts))
    textures: list[str] = []
    if ngroups and not r.eof():
        ntex = r.u32()
        if ntex:
            width = r.u32()
            if ntex > len(data):
                raise FormatError(".fnc: texture count is larger than the file")
            r.need(ntex * width)
            textures = [lossy(r.take(width).split(b"\0", 1)[0]) for _ in range(ntex)]
    return FncFile(groups, textures)
