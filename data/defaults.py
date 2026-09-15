"""SCIMAP land-cover classes, remap tables and default risk weights.

Lifted from ``config.yaml`` in the SCIMAP web application so the plugin carries
the same defaults without needing the app's configuration file.
"""

# SCIMAP land-cover classes shown in the Parameters table.
SCIMAP_CLASSES = [
    (1, "Woodland"),
    (2, "Arable"),
    (3, "Improved Grassland"),
    (4, "Extensive Grassland"),
    (5, "Moorland"),
    (6, "Urban"),
    (7, "Other"),
]

SCIMAP_CLASS_NAMES = dict(SCIMAP_CLASSES)

# Any valid land-cover value not explicitly listed in CEH_TO_SCIMAP is
# assigned to this SCIMAP class.
UNMAPPED_FALLBACK_CLASS = 7

# CEH Land Cover Map class IDs (1-23) -> SCIMAP classes (1-7).
CEH_TO_SCIMAP = {
    1: 1,   # Broadleaved woodland -> Woodland
    2: 1,   # Coniferous woodland -> Woodland
    3: 2,   # Arable and horticulture -> Arable
    4: 3,   # Improved grassland -> Improved Grassland
    5: 4,   # Rough grassland -> Extensive Grassland
    6: 4,   # Neutral grassland -> Extensive Grassland
    7: 4,   # Calcareous grassland -> Extensive Grassland
    8: 4,   # Acid grassland -> Extensive Grassland
    9: 5,   # Fen, marsh, swamp -> Moorland
    10: 5,  # Heather -> Moorland
    11: 5,  # Heather grassland -> Moorland
    12: 5,  # Bog -> Moorland
    13: 5,  # Montane habitats -> Moorland
    14: 7,  # Inland rock -> Other
    15: 7,  # Saltwater -> Other
    16: 7,  # Freshwater -> Other
    17: 7,  # Supra-littoral rock -> Other
    18: 7,  # Supra-littoral sediment -> Other
    19: 7,  # Littoral rock -> Other
    20: 7,  # Littoral sediment -> Other
    21: 7,  # Saltmarsh -> Other
    22: 6,  # Urban -> Urban
    23: 6,  # Suburban -> Urban
}

# Default SCIMAP Sediment risk weights by SCIMAP class ID.
DEFAULT_WEIGHTS = {
    1: 0.2,   # Woodland
    2: 1.0,   # Arable
    3: 0.3,   # Improved Grassland
    4: 0.15,  # Extensive Grassland
    5: 0.3,   # Moorland
    6: 0.5,   # Urban
    7: 0.5,   # Other
}

# Flood runoff-generation weighting by SCIMAP class ID; multipliers applied to
# runoff generation potential.
FLOOD_RUNOFF_WEIGHTS = {
    1: 0.2,   # Woodland
    2: 1.0,   # Arable
    3: 0.3,   # Improved Grassland
    4: 0.15,  # Extensive Grassland
    5: 0.3,   # Moorland
    6: 0.5,   # Urban
    7: 0.5,   # Other
}

# Legacy CEH labels, retained so parameter-set XML exported against raw LCM IDs
# can still be labelled sensibly on import.
LEGACY_LANDCOVER_CLASSES = [
    (1, "Broadleaved woodland"),
    (2, "Coniferous woodland"),
    (3, "Arable and horticulture"),
    (4, "Improved grassland"),
    (5, "Semi-natural grassland"),
    (6, "Mountain, heath, bog"),
    (7, "Saltwater"),
    (8, "Freshwater"),
    (9, "Coastal"),
    (10, "Built-up areas and gardens"),
]

# FIO normalisation constant (CFU reference value) used by SCIMAP FIO.
FIO_NORMALISE = 3.5e13

# Colour ramps offered for result styling; matches the web application's
# whitelist in processing/ows_utils.py.
COLOUR_RAMPS = [
    "Magma", "Viridis", "Plasma", "Inferno", "Cividis", "Spectral", "Turbo",
    "Blue-Red",
]

# Offered as the first enum entry: keep the web application's per-layer ramps
# rather than forcing one ramp across every output.
RAMP_DEFAULTS_OPTION = "SCIMAP defaults (per layer)"

RAMP_EROSION = "Magma"
RAMP_CONNECTIVITY = "Viridis"
RAMP_SCIMAP = "Plasma"
RAMP_FLOOD = "Spectral"
RAMP_LANDCOVER = "Viridis"
RAMP_OFD = "Cividis"


def default_remap_matrix():
    """CEH_TO_SCIMAP flattened for a QgsProcessingParameterMatrix default."""
    matrix = []
    for src in sorted(CEH_TO_SCIMAP):
        matrix.extend([src, CEH_TO_SCIMAP[src]])
    return matrix


def default_weights_matrix(weights=None):
    """DEFAULT_WEIGHTS flattened for a QgsProcessingParameterMatrix default."""
    weights = weights or DEFAULT_WEIGHTS
    matrix = []
    for class_id, _name in SCIMAP_CLASSES:
        matrix.extend([class_id, weights.get(class_id, 0.0)])
    return matrix
