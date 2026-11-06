"""`.lbs` extra visuals (lanes, sky, interactive, water) and texture export. Test-only writers build the bytes."""

import struct
import zipfile

from rbr2stj import textures
from rbr2stj.readers import lbs_extras as X
from rbr2stj.readers.chunks import Chunk, write_chunk, read_chunks


def p32(*v):
    return struct.pack(f"<{len(v)}I", *v)


def verts(n, stride):
    out = b""
    for i in range(n):
        v = struct.pack("<3f", float(i), 2.0 * i, 3.0 * i) + struct.pack("<I", 0xFF102030) + struct.pack("<2f", 0.25 * i, 0.5)
        out += v + b"\x00" * (stride - len(v))
    return out


def tris(n=1):
    return b"".join(struct.pack("<3H", 3 * k, 3 * k + 1, 3 * k + 2) for k in range(n))


def sky_chunk(name=b"MAPS/X/SKY.KSH\0", dome=False):
    item = (struct.pack("<3f", 1, 2, 3) if dome else b"") + p32(0x15, 49, 7, 24, 5, 3) + tris() + p32(3) + verts(3, 24)
    return name + p32(1) + item


def lane_chunk():
    item = p32(0x15, 1, 5, 0, 24, 9, 3) + tris() + b"\x00" + p32(3) + verts(3, 24) + struct.pack("<6f", *range(6))
    return p32(1) + p32(1) + p32(1) + item + p32(0)


def chunks(*pairs):
    data = b"".join(write_chunk(k, payload) for k, payload in pairs)
    return read_chunks(data, "test")


def test_sky_clouds_and_lanes_parse():
    e = X.parse(chunks(((1, 2), sky_chunk()), ((1, 3), sky_chunk(dome=True)), ((1, 1), lane_chunk())))
    groups = [i.group for i in e.items]
    assert groups == ["clouds", "sky", "lanes"]
    cloud, sky, lane = e.items
    assert cloud.textures == [7] and cloud.stride == 24 and cloud.indices == [0, 1, 2]
    assert cloud.positions[2] == (2.0, 4.0, 6.0) and cloud.uvs[1] == (0.25, 0.5) and cloud.colours[0] == 0xFF102030
    assert sky.centre == (1.0, 2.0, 3.0) and cloud.name == "MAPS/X/SKY.KSH"
    assert lane.textures == [5] and lane.kind == 1 and lane.extra["bounds"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_surface_and_interactive_parse():
    water = b"WATER\0" + b"\x01" + p32(1) + p32(0x10, 3, 11, 24, 0) + tris()[:0] + p32(3) + tris() + p32(3) + verts(3, 24)
    water = p32(1) + water
    inter = b"CLOCK\0" + b"\x00" + p32(1) + p32(0x10, 0, 4, 24, 0)[:0]
    draw = p32(0x10, 0, 4, 24, 3, 0)  # flags, unknown, texture 0, vertex size, ... built below
    draw = p32(0x10, 0, 4, 24, 0) + p32(3) + tris() + p32(3) + verts(3, 24)
    matrix = struct.pack("<16f", *[1 if i % 5 == 0 else 0 for i in range(16)])
    inter = p32(1) + b"CLOCK\0" + b"\x00" + p32(1) + draw + p32(1) + p32(9) + matrix
    e = X.parse(chunks(((1, 8), water), ((1, 6), inter)))
    w = next(i for i in e.items if i.group == "water")
    assert w.name == "WATER" and w.extra["key10"] == [11] and len(w.indices) == 3
    c = next(i for i in e.items if i.group == "interactive")
    assert c.textures == [4] and c.extra["visibility"][0]["word"] == 9


def test_texture_export_copies_bytes_unchanged(tmp_path):
    payload = b"DDS not really, just bytes" * 5
    z = tmp_path / "track-9_O_textures.rbz"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("track-9_O_textures/dry/new/ROAD01.dds", payload)
        zf.writestr("track-9_O_textures/wet/ROAD01.dds", b"wet one")
    src = textures.TextureSource([z], [], ["track-9_o_textures"])
    exp = textures.TextureExporter(src, tmp_path / "out" / "textures")
    res = exp.export("MAPS\\ARCTIC\\TEXTURES\\ROAD01.tga")          # a .tga request resolves to the .dds, dry first
    assert res is not None and res[0] == "textures/road01.dds"
    assert (tmp_path / "out" / res[0]).read_bytes() == payload
    assert exp.export("missing.tga") is None and "missing.tga" in exp.summary()["missing_or_failed"]
