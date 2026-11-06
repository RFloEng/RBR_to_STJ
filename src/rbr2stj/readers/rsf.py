"""RallySimFans (RSF) stage metadata: `Tracks<id>.ini`, `TrackSettings<id>.ini` weather rows, retail `Tracks.ini`.

Format: reRBR notes/formats.md "RSF stages" and the doc comments of crates/rbr-formats/src/rsf/stages.rs.

  Tracks<id>.ini [Map<id>]: TrackName, Particles, StageName, Surface (0 tarmac, 1 gravel, 2 snow), Length (km),
      Author, CountryCode, DefaultWetness (dry/damp/wet or 0/1/2), AnimationPath
  TrackSettings<id>.ini: the section names are the weathers: `<id><L>_[<cloud letter>_]<preset>_<cloud>`
      presets: crisp hazy norain lightrain heavyrain nosnow lightsnow heavysnow lightfog heavyfog
      clouds: clear partcloud lightcloud heavycloud
  Maps/Tracks.ini [Map<NN>] TrackName: retail stage names.

Note: PLAN.md had the Surface digits as 0 gravel, 1 tarmac; reRBR's reader (`Surface` parse in rsf/stages.rs) maps
0 tarmac, 1 gravel, 2 snow, and so do we (see docs/mapping.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import guard, ini
from .stage_files import in_dir

LETTERS = "MNEOS"
PRESETS = ["crisp", "hazy", "norain", "lightrain", "heavyrain", "nosnow", "lightsnow", "heavysnow", "lightfog", "heavyfog"]
CLOUDS = ["clear", "partcloud", "lightcloud", "heavycloud"]
SURFACES = {"0": "tarmac", "1": "gravel", "2": "snow"}
WETNESS = {"dry": 0, "0": 0, "damp": 1, "1": 1, "wet": 2, "2": 2}


@dataclass
class WeatherRow:
    section: str
    letter: str
    cloud_letter: str | None
    preset: str
    cloud: str
    settings: dict = field(default_factory=dict)     # every key of the TrackSettings section, verbatim


@dataclass
class RsfInfo:
    id: int
    name: str | None = None
    track_name: str | None = None
    particles: str | None = None
    surface: str | None = None
    length_km: float | None = None
    author: str | None = None
    country: str | None = None
    default_wetness: int | None = None
    animation_path: str | None = None
    weathers: list[WeatherRow] = field(default_factory=list)


def parse_weather(stage_id: int, section: str) -> WeatherRow | None:
    """`<id><L>_[<cloud letter>_]<preset>_<cloud>` (WeatherRow::parse); None if it does not fit."""
    m = re.match(rf"^{stage_id}([A-Za-z])_(.+)$", section.strip())
    if not m or m.group(1).upper() not in LETTERS:
        return None
    words = m.group(2).split("_")
    if len(words) == 3 and len(words[0]) == 1 and words[0].upper() in LETTERS:
        cl, preset, cloud = words[0].upper(), words[1].lower(), words[2].lower()
    elif len(words) == 2:
        cl, preset, cloud = None, words[0].lower(), words[1].lower()
    else:
        return None
    if preset not in PRESETS or cloud not in CLOUDS:
        return None
    return WeatherRow(section.strip(), m.group(1).upper(), cl, preset, cloud)


def read_folder(folder: Path) -> RsfInfo | None:
    """Read `Tracks<id>.ini` (+ `TrackSettings<id>.ini`) in a stage folder; None if there is no Tracks<id>.ini."""
    tracks = None
    for e in folder.iterdir():
        low = e.name.lower()
        if low.startswith("tracks") and low.endswith(".ini") and low[6:-4].isdigit():
            tracks = e
            break
    if tracks is None:
        return None
    sections = ini.parse(guard.read_file(tracks))
    sec = next((s for s in sections if s.name.lower().startswith("map") and s.name[3:].strip().isdigit()), None)
    if sec is None:
        return None
    sid = int(sec.name[3:].strip())
    g = lambda k: ini.unquote(sec.get(k)) or None  # noqa: E731
    info = RsfInfo(sid)
    info.name = g("StageName")
    tn = g("TrackName")
    info.track_name = tn.replace("\\", "/").rsplit("/", 1)[-1] if tn else None
    info.particles = g("Particles")
    surf = g("Surface")
    info.surface = SURFACES.get(surf.lower(), surf.lower() if surf and surf.lower() in SURFACES.values() else None) if surf else None
    try:
        info.length_km = float(g("Length")) if g("Length") else None
    except ValueError:
        info.length_km = None
    info.author = g("Author")
    info.country = g("CountryCode")
    wet = g("DefaultWetness")
    info.default_wetness = WETNESS.get(wet.lower()) if wet else None
    info.animation_path = g("AnimationPath")
    ts = in_dir(folder, f"TrackSettings{sid}.ini")
    if ts is not None:
        for s in ini.parse(guard.read_file(ts)):
            row = parse_weather(sid, s.name)
            if row:
                row.settings = {k: v for k, v in s.entries}
                info.weathers.append(row)
    return info


def retail_name(maps_dir: Path, number: int) -> str | None:
    """`Maps/Tracks.ini` [Map<NN>] TrackName, if the file is next to the stage."""
    p = in_dir(maps_dir, "Tracks.ini")
    if p is None:
        return None
    v = ini.get(ini.parse(guard.read_file(p)), f"Map{number}", "TrackName") or ini.get(
        ini.parse(guard.read_file(p)), f"Map{number:02d}", "TrackName")
    return ini.unquote(v)
