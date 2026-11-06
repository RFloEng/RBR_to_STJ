"""Source-neutral intermediate representation: what both the classic and the BTB converter produce and the STJ writers
consume. Coordinates are already in the STJ frame (right-handed, Z up, metres) and chainage is already shifted to
start at 0, so the writers never need to know which game format a stage came from."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Line:
    points: list[tuple[float, float, float]]
    tangents: list[tuple[float, float, float]] | None
    chainage: list[float]                       # starts at 0
    chainage_offset: float = 0.0                # stored distance of the first point (subtracted from every s)
    tangents_raw: list[tuple[float, float, float]] | None = None
    tangents_in: list[tuple[float, float, float]] | None = None   # tangents[i] starts segment i, tangents_in[i + 1] ends it


@dataclass
class Timing:
    start: float | None = None
    finish: float | None = None
    stop: float | None = None
    splits: list[float] = field(default_factory=list)


@dataclass
class Pose:
    position: tuple[float, float, float]
    heading: float                              # rad, CCW from +X


@dataclass
class Info:
    name: str
    surface: str | None = None                  # tarmac | gravel | snow
    length_m: float | None = None
    country: str | None = None
    author: str | None = None
    version: str | None = None
    date: str | None = None
    comment: str | None = None
    default_wetness: int | None = None          # 0 dry, 1 damp, 2 wet
    letter: str | None = None
    id: int | None = None


@dataclass
class SurfaceState:
    """One surface map: a (wear, wetness) state of the collision mesh."""
    key: str                                    # "new_dry"
    wear: str
    wetness: str
    palette: list[int]                          # RBR surface ids used in this state (palette index = position)
    layers: list[tuple[int, int, int]]          # per face: three palette indices
    weights: list[tuple[float, float]]          # per face corner (3 per face): (wa, wb), wc = 1 - wa - wb
    merged: list = field(default_factory=list)  # (wear, wetness) pairs this map stands for (identical states merge)


@dataclass
class CollisionMesh:
    points: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]
    states: list[SurfaceState] = field(default_factory=list)   # one state for BTB; up to nine for classic
    uvs: list[tuple[float, float]] | None = None               # faceVarying, 3 per face (BTB)


@dataclass
class WaterBody:
    kind: str                                   # puddle | pond | ford
    outline: list[tuple[float, float]]
    level: float
    when_wet: bool = False


@dataclass
class Prop:
    kind: str                                   # cone, block, sign, ... (RBR object kind name)
    name: str
    material: int
    dynamic: bool
    shape: tuple | None
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]
    transforms: list[tuple[float, float, float, float, float]]   # x, y, z, heading, scale
    placements_raw: list[dict] = field(default_factory=list)


@dataclass
class Fence:
    fence_type: int
    post_type: int
    material: int
    rail_material: int
    posts: list[tuple[float, float, float]]
    colours: list[int] = field(default_factory=list)


@dataclass
class Weather:
    settings: dict   # TrackSettings keys (fog, light, sky, cloud), verbatim
    section: str
    letter: str
    cloud_letter: str | None
    preset: str
    cloud: str


@dataclass
class Stage:
    kind: str                                   # rbr-classic | rbr-btb
    info: Info
    source: str
    line: Line
    timing: Timing
    raw_notes: list = field(default_factory=list)              # convert.pacenotes.RawNote
    notes: list = field(default_factory=list)                  # STJ pacenote dicts (sorted by s)
    spawn: Pose | None = None
    collision: CollisionMesh | None = None
    water: list[WaterBody] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)            # STJ edges (fences, invisible brake walls)
    props: list[Prop] = field(default_factory=list)
    fences: list[Fence] = field(default_factory=list)
    weathers: list[Weather] = field(default_factory=list)
    extra_meshes: list = field(default_factory=list)           # geometry.meshes entries written by other builders
    visual: list = field(default_factory=list)                 # BTB visual objects: {object, mesh, matrices}
    lbs_visual: list = field(default_factory=list)             # classic .lbs (1,0) GeometryBlocks
    texture_source: object = None                              # textures.TextureSource (None: textures not exported)
    lbs_extras: list = field(default_factory=list)             # readers.lbs_extras.Item (lanes, sky, interactive, water)
    sky_files: list = field(default_factory=list)              # [(Path of a global sky .lbs, its Texture<N> map, items)]
    texture_requests: dict = field(default_factory=dict)       # stage .ini Texture<N> -> raw value
    btb_track: object = None                                  # the readers.btb.BtbTrack (materials for visual meshes)
    shift: tuple = (0.0, 0.0)                                  # x/y origin shift applied to every coordinate
    physics: dict | None = None                                # readers.physics_lsp.load(): surface and tyre tables
    x_btb: dict = field(default_factory=dict)
    origin_offset: tuple[float, float, float] | None = None
    sun_direction: tuple[float, float, float] | None = None
    x_rbr: dict = field(default_factory=dict)
    lost: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    file_hashes: dict = field(default_factory=dict)            # ext -> sha256 of the source file (variant sharing)
