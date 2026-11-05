"""Surface id -> name -> STJ class. The ids are the `physics.lsp` "surface types" keys.

Source of the id/name facts: reRBR crates/rbr-formats/src/lsp_keys.rs (`MaterialManager` keys) and
data/physics-lsp-fields.md; the two agree on all 134 entries (checked by tools/extract_surface_ids.py).
The class mapping below is ours (rbr2stj): STJ has no grip numbers for these because RBR grip lives in the NGP
physics, not in the stage files.
"""

from __future__ import annotations

NAMES: dict[int, str] = {
    0: 'Undefined', 1: 'Gravel_Fine_Deeper_Dry', 2: 'Gravel_Fine_Deeper_Damp', 3: 'Gravel_Fine_Deeper_Wet',
    5: 'Gravel_Medium_Deeper_Dry', 6: 'Gravel_Medium_Deeper_Damp', 7: 'Gravel_Medium_Deeper_Wet',
    9: 'Gravel_Fine_Deep_Dry', 10: 'Gravel_Fine_Deep_Damp', 11: 'Gravel_Fine_Deep_Wet', 13: 'Gravel_Medium_Deep_Dry',
    14: 'Gravel_Medium_Deep_Damp', 15: 'Gravel_Medium_Deep_Wet', 17: 'Gravel_Fine_Shallow_Dry',
    18: 'Gravel_Fine_Shallow_Damp', 19: 'Gravel_Fine_Shallow_Wet', 21: 'Gravel_Medium_Shallow_Dry',
    22: 'Gravel_Medium_Shallow_Damp', 23: 'Gravel_Medium_Shallow_Wet', 24: 'Spret_Bush_Small',
    25: 'Spret_Bush_Medium', 26: 'Spret_Bush_Large', 27: 'Lov_Bush_Small', 28: 'Lov_Bush_Medium',
    29: 'Lov_Bush_Large', 30: 'Tree_Small', 31: 'Tree_Medium', 32: 'Tree_Large', 33: 'Gravel_Coarse_Deeper_Dry',
    34: 'Gravel_Coarse_Deeper_Damp', 35: 'Gravel_Coarse_Deeper_Wet', 37: 'Tarmac_Fine_Clean_Dry',
    38: 'Tarmac_Fine_Clean_Damp', 39: 'Tarmac_Fine_Clean_Wet', 41: 'Gravel_Coarse_Deep_Dry', 42: 'Gravel_Coarse_Deep_Damp',
    43: 'Gravel_Coarse_Deep_Wet', 45: 'Tarmac_Fine_Sprinkled_Dry', 46: 'Tarmac_Fine_Sprinkled_Damp',
    47: 'Tarmac_Fine_Sprinkled_Wet', 49: 'Gravel_Coarse_Shallow_Dry', 50: 'Gravel_Coarse_Shallow_Damp',
    51: 'Gravel_Coarse_Shallow_Wet', 53: 'Tarmac_Fine_Covered_Dry', 54: 'Tarmac_Fine_Covered_Damp',
    55: 'Tarmac_Fine_Covered_Wet', 56: 'Rock_Small', 57: 'Rock_Medium', 58: 'Rock_Large', 59: 'Trunk_Small',
    60: 'Trunk_Medium', 61: 'Trunk_Large', 62: 'Snowwall', 63: 'Metal_Pole', 64: 'Metal_Barrier',
    65: 'Tarmac_Medium_Clean_Dry', 66: 'Tarmac_Medium_Clean_Damp', 67: 'Tarmac_Medium_Clean_Wet',
    69: 'Tarmac_Coarse_Clean_Dry', 70: 'Tarmac_Coarse_Clean_Damp', 71: 'Tarmac_Coarse_Clean_Wet',
    73: 'Tarmac_Medium_Sprinkled_Dry', 74: 'Tarmac_Medium_Sprinkled_Damp', 75: 'Tarmac_Medium_Sprinkled_Wet',
    77: 'Tarmac_Coarse_Sprinkled_Dry', 78: 'Tarmac_Coarse_Sprinkled_Damp', 79: 'Tarmac_Coarse_Sprinkled_Wet',
    81: 'Tarmac_Medium_Covered_Dry', 82: 'Tarmac_Medium_Covered_Damp', 83: 'Tarmac_Medium_Covered_Wet',
    85: 'Tarmac_Coarse_Covered_Dry', 86: 'Tarmac_Coarse_Covered_Damp', 87: 'Tarmac_Coarse_Covered_Wet',
    88: 'Snowwall_Bottom', 97: 'Grass_Hard_Dry', 98: 'Grass_Hard_Damp', 99: 'Grass_Hard_Wet',
    101: 'SnowOnGravel_Shallow', 102: 'SnowOnGravel_Medium', 103: 'SnowOnGravel_Deep', 105: 'Grass_Medium_Dry',
    106: 'Grass_Medium_Damp', 107: 'Grass_Medium_Wet', 113: 'Grass_Soft_Dry', 114: 'Grass_Soft_Damp',
    115: 'Grass_Soft_Wet', 117: 'SnowOnIce_Shallow', 118: 'SnowOnIce_Medium', 119: 'SnowOnIce_Deep',
    120: 'Tree_Stump', 121: 'Bendable_Tree', 129: 'Rough_Rough_Dry', 130: 'Rough_Rough_Wet',
    133: 'Dirt_Hard_Dry', 134: 'Dirt_Hard_Damp', 135: 'Dirt_Hard_Wet', 137: 'Rough_VeryRough_Dry',
    138: 'Rough_VeryRough_Wet', 141: 'Dirt_Medium_Dry', 142: 'Dirt_Medium_Damp', 143: 'Dirt_Medium_Wet',
    149: 'Dirt_Soft_Dry', 150: 'Dirt_Soft_Damp', 151: 'Dirt_Soft_Wet', 153: 'Gravel_Fine_Shallow_Dry_Clone',
    154: 'Gravel_Fine_Shallow_Damp_Clone', 155: 'Gravel_Fine_Shallow_Wet_Clone', 157: 'Water_Surface',
    161: 'Dirt_Hard_Dry_Clone', 162: 'Dirt_Hard_Damp_Clone', 163: 'Dirt_Hard_Wet_Clone', 165: 'Very_Sparse_Bush',
    166: 'Sparse_Bush', 167: 'Dense_Bush', 168: 'Very_Dense_Bush', 177: 'US2_Gravel_Fine_Shallow_Dry',
    178: 'US2_Gravel_Fine_Shallow_Damp', 179: 'US2_Gravel_Fine_Shallow_Wet', 181: 'BR2_Gravel_Coarse_Shallow_Dry',
    182: 'BR2_Gravel_Coarse_Shallow_Damp', 183: 'BR2_Gravel_Coarse_Shallow_Wet', 185: 'US2_Gravel_Fine_Deep_Dry',
    186: 'US2_Gravel_Fine_Deep_Damp', 187: 'US2_Gravel_Fine_Deep_Wet', 189: 'BR2_Gravel_Coarse_Deep_Dry',
    190: 'BR2_Gravel_Coarse_Deep_Damp', 191: 'BR2_Gravel_Coarse_Deep_Wet', 193: 'US2_Gravel_Fine_Deeper_Dry',
    194: 'US2_Gravel_Fine_Deeper_Damp', 195: 'US2_Gravel_Fine_Deeper_Wet', 197: 'BR2_Gravel_Coarse_Deeper_Dry',
    198: 'BR2_Gravel_Coarse_Deeper_Damp', 199: 'BR2_Gravel_Coarse_Deeper_Wet', 255: 'Spectator',
}

SPECTATOR = 255


def name(sid: int) -> str:
    return NAMES.get(sid, "Undefined" if sid == 0 else f"Unknown_{sid}")


def stj_id(sid: int) -> str:
    """Surface library id: `rbr_<lowercase name>`; unknown ids become `rbr_unknown_<n>`."""
    return "rbr_" + name(sid).lower()


def classify(sid: int) -> tuple[str, str | None]:
    """(STJ surface class, impact response or None) for a surface id, from its name."""
    n = name(sid).lower()
    if n.startswith(("gravel", "us2_gravel", "br2_gravel")):
        return "gravel", None
    if n.startswith("tarmac"):
        return "asphalt", None
    if n.startswith("grass"):
        return "grass", None
    if n.startswith(("dirt", "rough")):
        return "dirt", None
    if n.startswith(("snowon", "snowwall")):
        return "snow", ("rigid" if n.startswith("snowwall") else None)
    if n.startswith("rock"):
        return "rock", "rigid"
    if n.startswith("water"):
        return "water", None
    if n.startswith("metal"):
        return "metal", "rigid"
    if n.startswith("bendable_tree"):
        return "other", "bendable"
    if n.startswith(("tree_stump", "trunk", "tree_")):
        return "other", "rigid"
    if "bush" in n:
        return "other", "soft"
    return "other", None
