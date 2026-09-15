"""Erosion / runoff-generation risk and percentile normalisation.

Ported from ``processing/wb_utils.py`` (``compute_erosion_risk``,
``normalise_percentile``) in the SCIMAP web application.
"""

import numpy as np


def compute_erosion_risk(accum_array, slope_deg, cell_area, use_stream_power=True):
    """Compute erosion risk matching SCIMAP core with optional stream power."""
    adjusted = np.abs(accum_array) * cell_area
    if not use_stream_power:
        risk = adjusted
        risk[~np.isfinite(risk)] = np.nan
        return risk

    # Avoid tan(90 deg) singularities creating inf/NaN stripes in outputs.
    slope_safe = np.clip(slope_deg, 0.0, 89.0)
    slope_rad = slope_safe * np.pi / 180.0
    risk = adjusted * np.tan(slope_rad)
    risk[~np.isfinite(risk)] = np.nan
    return risk


def normalise_percentile(array, p_low=5, p_high=95):
    """Rescale *array* to [0, 1] between its p_low and p_high percentiles."""
    valid = np.isfinite(array)
    if not valid.any():
        return np.full_like(array, np.nan)

    lo, hi = np.nanpercentile(array[valid], [p_low, p_high])
    if hi <= lo:
        hi = lo + 1.0
    # np.nanpercentile always returns float64 scalars, which would silently
    # upcast a float32 input (and every array derived from this result) to
    # float64. Cast back to the input's own dtype so callers that work in
    # float32 stay in float32 throughout.
    lo = array.dtype.type(lo)
    hi = array.dtype.type(hi)
    result = np.clip((array - lo) / (hi - lo), 0.0, 1.0)
    result[~valid] = np.nan
    return result
