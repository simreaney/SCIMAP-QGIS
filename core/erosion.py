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


def percentile_range(array, p_low=5, p_high=95, max_samples=None):
    """Return the (p_low, p_high) percentiles of the finite values in *array*.

    ``array[valid]`` allocates a copy of every finite cell, which on a large
    grid is a second full-raster array purely to take two percentiles. When
    *max_samples* is given and the grid is bigger than that, stride through the
    flattened array instead so the working set stays bounded. The stride is
    deterministic, so repeated runs over the same raster agree exactly — which
    matters because this normalisation is part of the calibrated quantity in
    SCIMAP-Fitted, not just a display stretch.

    Returns ``(lo, hi)`` as plain floats, or ``(nan, nan)`` when nothing is
    finite.
    """
    flat = np.ravel(array)
    sample = None
    if max_samples is not None and flat.size > max_samples:
        stride = int(np.ceil(flat.size / max_samples))
        strided = flat[::stride]
        strided = strided[np.isfinite(strided)]
        # Sparse valid data on an unlucky stride can miss every finite cell.
        # Fall back to the exact pass rather than returning NaN cut points,
        # which would silently blank the whole output raster.
        if strided.size:
            sample = strided

    if sample is None:
        sample = flat[np.isfinite(flat)]
    if sample.size == 0:
        return float('nan'), float('nan')

    lo, hi = np.percentile(sample, [p_low, p_high])
    return float(lo), float(hi)


def normalise_percentile(array, p_low=5, p_high=95, max_samples=None):
    """Rescale *array* to [0, 1] between its p_low and p_high percentiles.

    *max_samples* bounds the memory used to find the percentiles; see
    :func:`percentile_range`. It does not change which cells are rescaled, only
    how the two cut points are estimated.
    """
    valid = np.isfinite(array)
    if not valid.any():
        return np.full_like(array, np.nan)

    lo, hi = percentile_range(array, p_low, p_high, max_samples)
    if hi <= lo:
        hi = lo + 1.0
    # np.percentile always returns float64 scalars, which would silently
    # upcast a float32 input (and every array derived from this result) to
    # float64. Cast back to the input's own dtype so callers that work in
    # float32 stay in float32 throughout.
    lo = array.dtype.type(lo)
    hi = array.dtype.type(hi)
    result = np.clip((array - lo) / (hi - lo), 0.0, 1.0)
    result[~valid] = np.nan
    return result
