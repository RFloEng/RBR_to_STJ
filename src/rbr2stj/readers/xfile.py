"""DirectX binary `.x` mesh reader (the 32-bit `xof 0303bin 0032` form BTB stages use).

Format: Microsoft's documented binary `.x` token stream, as summarised in reRBR notes/btb.md "Meshes" (every
BTB `.x` starts `xof 0303bin 0032`; one top-level unnamed `Mesh`, triangles only, one material, optional UVs).

Tokens are u16 numbers: 1 NAME (u32 length, bytes), 2 STRING (u32 length, bytes), 3 INTEGER (u32), 5 GUID (16 bytes),
6 INTEGER_LIST (u32 count, u32 values), 7 FLOAT_LIST (u32 count, f32 values), 10/11 braces, 12-20 separators,
31 TEMPLATE, 40-52 type keywords. Adjacent values of one kind merge into one list, so a data object's body is read as
one stream of integers, one of floats and child objects, and the standard templates fix what each stream means:

  Mesh                : ints [nVertices, nFaces, (3, a, b, c)*]; floats [x y z]*nVertices   (faces other than
                        triangles are fanned)
  MeshTextureCoords   : ints [n]; floats [u v]*n
  MeshMaterialList    : ints [nMaterials, nFaceIndexes, index*]; children Material
  Material            : child TextureFilename (string)
  Everything else (MeshNormals, VertexDuplicationIndices, DeclData, FVFData, ...) is skipped.

Text (`txt `), 64-bit (`0064`) and compressed (`bzip`, `tzip`) files are reported as unsupported.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binio import Reader, lossy
from ..errors import FormatError

T_NAME, T_STRING, T_INTEGER, T_GUID, T_INT_LIST, T_FLOAT_LIST = 1, 2, 3, 5, 6, 7
T_OBRACE, T_CBRACE, T_TEMPLATE = 10, 11, 31


@dataclass
class Mesh:
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    uvs: list[tuple[float, float]] | None                  # one per vertex, or None (no texture coordinates)
    material_names: list[str]                              # texture filenames, one per material
    face_materials: list[int] = field(default_factory=list)  # per triangle


@dataclass
class _Obj:
    kind: str
    ints: list[int] = field(default_factory=list)
    floats: list[float] = field(default_factory=list)
    strings: list[str] = field(default_factory=list)
    children: list["_Obj"] = field(default_factory=list)


def _tokens(data: bytes, label: str):
    if data[:4] != b"xof ":
        raise FormatError(f"{label}: not a DirectX .x file (no 'xof ' header)")
    if len(data) < 16:
        raise FormatError(f"{label}: truncated header")
    form, fsize = data[8:12], data[12:16]
    if form != b"bin ":
        raise FormatError(f"{label}: unsupported .x form {form!r} (only binary 'bin ' is read)")
    if fsize != b"0032":
        raise FormatError(f"{label}: unsupported .x float size {fsize!r} (only 0032 is read)")
    r = Reader(data, 16, label=label)
    while not r.eof():
        t = r.u16()
        if t == T_NAME or t == T_STRING:
            yield t, lossy(r.take(r.u32()).rstrip(b"\0"))
        elif t == T_INTEGER:
            yield t, r.u32()
        elif t == T_GUID:
            yield t, r.take(16)
        elif t == T_INT_LIST:
            n = r.u32()
            r.need(4 * n)
            yield t, list(r.u32s(n))
        elif t == T_FLOAT_LIST:
            n = r.u32()
            r.need(4 * n)
            yield t, list(r.f32s(n))
        elif t in (T_OBRACE, T_CBRACE, T_TEMPLATE) or 12 <= t <= 20 or 40 <= t <= 52:
            yield t, None
        else:
            raise FormatError(f"{label}: unknown token {t} at offset {r.pos - 2}")


def _parse_objects(data: bytes, label: str) -> list[_Obj]:
    toks = list(_tokens(data, label))
    i = 0
    n = len(toks)

    def skip_braced(j: int) -> int:
        """j is at the token before OBRACE (or at OBRACE); return the index after the matching CBRACE."""
        while j < n and toks[j][0] != T_OBRACE:
            j += 1
        depth = 0
        while j < n:
            if toks[j][0] == T_OBRACE:
                depth += 1
            elif toks[j][0] == T_CBRACE:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise FormatError(f"{label}: unbalanced braces")

    def body(j: int, obj: _Obj) -> int:
        """Parse tokens after an object's OBRACE up to its CBRACE; return the index after it."""
        while j < n:
            t, v = toks[j]
            if t == T_CBRACE:
                return j + 1
            if t == T_INT_LIST:
                obj.ints += v
            elif t == T_INTEGER:
                obj.ints.append(v)
            elif t == T_FLOAT_LIST:
                obj.floats += v
            elif t == T_STRING:
                obj.strings.append(v)
            elif t == T_NAME:
                # a child object: NAME [NAME] OBRACE ...   or a reference { NAME }
                k = j + 1
                if k < n and toks[k][0] == T_NAME:
                    k += 1
                if k < n and toks[k][0] == T_OBRACE:
                    child = _Obj(v)
                    j = body(k + 1, child)
                    obj.children.append(child)
                    continue
            j += 1
        raise FormatError(f"{label}: object '{obj.kind}' is not closed")

    top: list[_Obj] = []
    while i < n:
        t, v = toks[i]
        if t == T_TEMPLATE:
            i = skip_braced(i)
            continue
        if t == T_NAME:
            k = i + 1
            if k < n and toks[k][0] == T_NAME:
                k += 1
            if k < n and toks[k][0] == T_OBRACE:
                obj = _Obj(v)
                i = body(k + 1, obj)
                top.append(obj)
                continue
        i += 1
    return top


def parse(data: bytes, label: str = ".x") -> Mesh:
    objs = _parse_objects(data, label)
    mesh = next((o for o in objs if o.kind == "Mesh"), None)
    if mesh is None:
        raise FormatError(f"{label}: no Mesh object")
    ints, floats = mesh.ints, mesh.floats
    if not ints:
        raise FormatError(f"{label}: Mesh has no vertex count")
    nv = ints[0]
    if len(floats) < 3 * nv:
        raise FormatError(f"{label}: Mesh declares {nv} vertices but holds {len(floats) // 3}")
    verts = [(floats[3 * k], floats[3 * k + 1], floats[3 * k + 2]) for k in range(nv)]
    if len(ints) < 2:
        raise FormatError(f"{label}: Mesh has no face count")
    nf = ints[1]
    tris: list[tuple[int, int, int]] = []
    pos = 2
    for _ in range(nf):
        if pos >= len(ints):
            raise FormatError(f"{label}: Mesh faces are truncated")
        m = ints[pos]
        idx = ints[pos + 1:pos + 1 + m]
        if len(idx) != m:
            raise FormatError(f"{label}: Mesh face is truncated")
        pos += 1 + m
        if any(x >= nv for x in idx):
            raise FormatError(f"{label}: a face indexes vertex {max(idx)} of {nv}")
        for q in range(1, m - 1):                     # fan polygons (BTB files hold triangles only)
            tris.append((idx[0], idx[q], idx[q + 1]))
    uvs = None
    for c in mesh.children:
        if c.kind == "MeshTextureCoords" and c.ints and len(c.floats) >= 2 * c.ints[0]:
            uvs = [(c.floats[2 * k], c.floats[2 * k + 1]) for k in range(c.ints[0])]
            if len(uvs) != nv:
                raise FormatError(f"{label}: {len(uvs)} texture coordinates for {nv} vertices")
    names: list[str] = []
    face_mats: list[int] = []
    for c in mesh.children:
        if c.kind == "MeshMaterialList":
            nm = c.ints[0] if c.ints else 0
            nfi = c.ints[1] if len(c.ints) > 1 else 0
            face_mats = c.ints[2:2 + nfi]
            for mc in c.children:
                if mc.kind == "Material":
                    tex = next((t for t in mc.children if t.kind == "TextureFilename"), None)
                    names.append(tex.strings[0] if tex and tex.strings else "")
            if len(names) < nm:
                names += [""] * (nm - len(names))
    return Mesh(verts, tris, uvs, names, face_mats)


# ── writer used by the test fixtures (kept here so the token numbers live in one file) ─────────

def write_binary(mesh: Mesh, with_templates: bool = True) -> bytes:
    """Serialise `mesh` as a binary 0032 `.x` file (test twin of `parse`; also handy for examples)."""
    out = bytearray(b"xof 0303bin 0032")

    def name(s: str) -> None:
        b = s.encode()
        out.extend(struct.pack("<HI", T_NAME, len(b)) + b)

    def string(s: str) -> None:
        b = s.encode()
        out.extend(struct.pack("<HI", T_STRING, len(b)) + b + struct.pack("<H", 20))

    def ints(vals) -> None:
        out.extend(struct.pack("<HI", T_INT_LIST, len(vals)) + struct.pack(f"<{len(vals)}I", *vals))

    def floats(vals) -> None:
        out.extend(struct.pack("<HI", T_FLOAT_LIST, len(vals)) + struct.pack(f"<{len(vals)}f", *vals))

    def tok(t: int) -> None:
        out.extend(struct.pack("<H", t))

    if with_templates:
        tok(T_TEMPLATE); name("Vector"); tok(T_OBRACE)
        out.extend(struct.pack("<H", T_GUID) + bytes(range(16)))
        tok(42); name("x"); tok(20); tok(42); name("y"); tok(20); tok(42); name("z"); tok(20)
        tok(T_CBRACE)
    name("Mesh"); tok(T_OBRACE)
    ints([len(mesh.vertices)])
    floats([c for v in mesh.vertices for c in v])
    fl = [len(mesh.triangles)]
    for t in mesh.triangles:
        fl += [3, *t]
    ints(fl)
    if mesh.uvs is not None:
        name("MeshTextureCoords"); tok(T_OBRACE)
        ints([len(mesh.uvs)])
        floats([c for uv in mesh.uvs for c in uv])
        tok(T_CBRACE)
    if mesh.material_names:
        name("MeshMaterialList"); tok(T_OBRACE)
        fm = mesh.face_materials or [0] * len(mesh.triangles)
        ints([len(mesh.material_names), len(fm), *fm])
        for tex in mesh.material_names:
            name("Material"); tok(T_OBRACE)
            floats([1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            name("TextureFilename"); tok(T_OBRACE); string(tex); tok(T_CBRACE)
            tok(T_CBRACE)
        tok(T_CBRACE)
    tok(T_CBRACE)
    return bytes(out)
