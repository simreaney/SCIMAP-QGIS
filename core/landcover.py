"""Land-cover remapping and risk-weight reclassification.

Ported from ``processing/scimap_standard.py`` and ``processing/scimap_flood.py``
in the SCIMAP web application (``_remap_landcover_ids``, ``_apply_reclass``,
``_find_unmapped_landcover_ids``, ``_build_reclass_lookup``).
"""

import numpy as np

from ..data.defaults import UNMAPPED_FALLBACK_CLASS


def remap_landcover_ids(landcover_array, remap):
    """Remap raw land-cover class IDs to SCIMAP class IDs."""
    if not remap:
        return landcover_array

    remapped = np.array(landcover_array, copy=True)
    for src_id, dst_id in remap.items():
        remapped[landcover_array == src_id] = dst_id
    return remapped


def apply_reclass(landcover_array, lookup):
    """Reclassify a land-cover array using a numeric lookup dict."""
    # float32 matches every other raster in the pipeline (and the risk
    # weighting is always written out as GDT_Float32 anyway), so there is
    # nothing to gain from computing this in float64.
    result = np.full_like(landcover_array, np.nan, dtype=np.float32)
    for lc_id, value in lookup.items():
        result[landcover_array == lc_id] = value
    return result


def find_unmapped_landcover_ids(landcover_array, lookup):
    """Return sorted land-cover IDs present in data but missing from *lookup*."""
    valid = np.isfinite(landcover_array) & (landcover_array > 0)
    if not valid.any():
        return []
    present_ids = np.unique(landcover_array[valid].astype(np.int64))
    return sorted(int(lc_id) for lc_id in present_ids if int(lc_id) not in lookup)


def build_risk_weight(landcover_array, weights, remap=None, already_scimap=True,
                      fallback_class=UNMAPPED_FALLBACK_CLASS, feedback=None):
    """Turn a land-cover raster into a per-cell risk weight raster.

    Returns ``(risk_weight, scimap_class_array)``. Cells whose class is valid but
    carries no weight are backfilled with the fallback class's weight so the
    output has no internal NoData holes, matching the web application.
    """
    scimap_array = (
        landcover_array if already_scimap
        else remap_landcover_ids(landcover_array, remap or {})
    )

    unmapped = find_unmapped_landcover_ids(scimap_array, weights)
    if unmapped and feedback is not None:
        feedback.pushWarning(
            f"Unassigned land-cover IDs in the catchment ({len(unmapped)}): "
            + ", ".join(str(v) for v in unmapped)
        )

    risk_weight = apply_reclass(scimap_array, weights)

    fallback_weight = weights.get(int(fallback_class))
    if fallback_weight is not None:
        missing = np.isnan(risk_weight) & np.isfinite(scimap_array) & (scimap_array > 0)
        if missing.any():
            if feedback is not None:
                feedback.pushInfo(
                    f"Applying fallback SCIMAP class {int(fallback_class)} weight to "
                    f"{int(np.count_nonzero(missing))} unmapped cells."
                )
            risk_weight[missing] = float(fallback_weight)

    return risk_weight, scimap_array


def matrix_to_lookup(matrix, value_type=float):
    """Convert a flat QgsProcessingParameterMatrix list into a dict.

    The matrix arrives as ``[key, value, key, value, ...]``; blank trailing rows
    added by the Processing table widget are skipped.
    """
    lookup = {}
    values = list(matrix or [])
    for idx in range(0, len(values) - 1, 2):
        key, value = values[idx], values[idx + 1]
        if key in (None, '') or value in (None, ''):
            continue
        try:
            lookup[int(float(key))] = value_type(float(value))
        except (TypeError, ValueError):
            continue
    return lookup


def matrix_to_bounds(matrix):
    """Convert a three-column QgsProcessingParameterMatrix into weight bounds.

    The matrix arrives flat as ``[class, min, max, class, min, max, ...]``;
    :func:`matrix_to_lookup` only understands two columns. Returns
    ``{class_id: (lower, upper)}``, skipping blank rows. A row whose upper bound
    is not above its lower bound is an error rather than something to silently
    clamp — it would collapse that class to a constant and quietly remove a
    dimension from the calibration.
    """
    bounds = {}
    values = list(matrix or [])
    for idx in range(0, len(values) - 2, 3):
        key, low, high = values[idx], values[idx + 1], values[idx + 2]
        if key in (None, '') or low in (None, '') or high in (None, ''):
            continue
        try:
            class_id = int(float(key))
            lower = float(low)
            upper = float(high)
        except (TypeError, ValueError):
            continue
        if not upper > lower:
            raise ValueError(
                f"Weight bounds for SCIMAP class {class_id} are not increasing: "
                f"min={lower:g}, max={upper:g}."
            )
        bounds[class_id] = (lower, upper)
    return bounds
