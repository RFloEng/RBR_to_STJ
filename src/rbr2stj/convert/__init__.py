"""Source formats -> IR (`rbr2stj.ir`)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Options:
    variant: str | None = None            # classic: track letter (M N E O S)
    all_variants: bool = False
    no_geometry: bool = False             # skip collision/prop/visual meshes (line, timing, notes only)
    surface_table: dict | None = None     # user JSON: {"<rbr surface id or name>": {mf_scaling, mu, ...}}
    surface_states: str = "all"           # "all": nine wear x wetness maps; "default": normal x (default wetness)
    mesh_format: str = "auto"             # usda | usdc | auto (usdc above 50k faces)
    visual: bool = True                   # BTB visual meshes / classic .lbs geometry when available
