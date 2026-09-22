"""Per-site, per-land-cover-class statistics inside a delineated catchment.

Ported from ``SCIMAP-Fitted-Step2.py``'s ``summarize_class_rows``, minus its
polygon round trip. The scripts polygonise each watershed raster and then
rasterize the polygon again to build a mask; the ``Watershed`` output *is* the
mask, so this works from it directly. That removes a whole rasterisation pass
and any disagreement between the raster and vector versions of a boundary.

Pure NumPy — no GDAL — so the grouped reduction can be tested on its own.
"""

import numpy as np


def basin_window(basin_array):
    """Bounding box ``(row0, row1, col0, col1)`` of a watershed's cells.

    Catchments are usually a small part of the clipped grid, and everything
    downstream only looks inside one at a time, so cropping to the basin's own
    window keeps the per-site work proportional to the basin rather than to the
    whole raster. Returns ``None`` when the watershed is empty.
    """
    rows = np.flatnonzero(np.any(basin_array > 0, axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(np.any(basin_array > 0, axis=0))
    return int(rows[0]), int(rows[-1] + 1), int(cols[0]), int(cols[-1] + 1)


#: Statistics that can be summarised per land-cover class, matching the
#: research scripts' ``--stats`` choices.
SUPPORTED_STATISTICS = ('mean', 'median', 'min', 'max', 'std', 'sum')


def class_statistics(class_array, value_array, valid_mask, pixel_area,
                     statistics=('mean',)):
    """Summarise *value_array* by land-cover class over the valid cells.

    Returns one dict per class present::

        {'land_cover_class', 'pixel_count', 'area_m2',
         'connectivity_x_erosion_<stat>' for each requested statistic,
         'mean_connectivity_x_erosion' when 'mean' is among them}

    The ``mean_connectivity_x_erosion`` alias is what the calibration reads by
    default, and is the column name the research scripts also emit, so their
    summary tables and these are interchangeable.

    ``pixel_count`` counts every valid cell of the class; the statistics are
    taken over those of them whose value is finite, and are NaN when none are.
    The two differ wherever connectivity or erosion is undefined inside an
    otherwise valid catchment, and conflating them would quietly shrink the
    class's area.
    """
    requested = [name for name in statistics if name in SUPPORTED_STATISTICS]
    unknown = [name for name in statistics if name not in SUPPORTED_STATISTICS]
    if unknown:
        raise ValueError(
            f"Unknown statistic(s) {unknown}; expected any of "
            f"{', '.join(SUPPORTED_STATISTICS)}.")
    if not requested:
        requested = ['mean']

    mask = np.asarray(valid_mask, dtype=bool)
    classes = np.asarray(class_array)[mask]
    values = np.asarray(value_array, dtype=np.float64)[mask]

    usable = np.isfinite(classes) & (classes > 0)
    classes = classes[usable].astype(np.int64, copy=False)
    values = values[usable]
    if classes.size == 0:
        return []

    # A single sort plus np.add.reduceat groups by class without a Python loop
    # per class; mergesort keeps it stable so repeated runs agree exactly.
    order = np.argsort(classes, kind='mergesort')
    classes = classes[order]
    values = values[order]

    unique, starts, counts = np.unique(
        classes, return_index=True, return_counts=True)

    finite = np.isfinite(values)
    finite_counts = np.add.reduceat(finite.astype(np.float64), starts)
    has_values = finite_counts > 0
    empty = np.full(unique.shape, np.nan)

    computed = {}
    totals = np.add.reduceat(np.where(finite, values, 0.0), starts)
    if 'sum' in requested:
        computed['sum'] = np.where(has_values, totals, np.nan)

    means = np.divide(totals, finite_counts, out=empty.copy(), where=has_values)
    if 'mean' in requested:
        computed['mean'] = np.where(has_values, means, np.nan)

    if 'std' in requested:
        # Population standard deviation (ddof=0), matching the scripts.
        sum_of_squares = np.add.reduceat(np.where(finite, values * values, 0.0), starts)
        mean_squares = np.divide(
            sum_of_squares, finite_counts, out=empty.copy(), where=has_values)
        variance = np.maximum(mean_squares - means * means, 0.0)
        computed['std'] = np.where(has_values, np.sqrt(variance), np.nan)

    # Neutral fill values so a NaN cell cannot win a min or max.
    if 'min' in requested:
        computed['min'] = np.where(
            has_values,
            np.minimum.reduceat(np.where(finite, values, np.inf), starts), np.nan)
    if 'max' in requested:
        computed['max'] = np.where(
            has_values,
            np.maximum.reduceat(np.where(finite, values, -np.inf), starts), np.nan)

    if 'median' in requested:
        medians = empty.copy()
        for index, start in enumerate(starts):
            group = values[start:start + counts[index]]
            group = group[np.isfinite(group)]
            if group.size:
                medians[index] = float(np.median(group))
        computed['median'] = medians

    rows = []
    for index, (class_id, count) in enumerate(zip(unique, counts)):
        row = {
            'land_cover_class': int(class_id),
            'pixel_count': int(count),
            'area_m2': float(count) * float(pixel_area),
        }
        for name in requested:
            row[f'connectivity_x_erosion_{name}'] = float(computed[name][index])
        if 'mean' in requested:
            # The alias the calibration reads by default, and the name the
            # research scripts use.
            row['mean_connectivity_x_erosion'] = float(computed['mean'][index])
        rows.append(row)
    return rows


def sample_array_at_cells(array, rows, cols, fallback=1.0):
    """Read *array* at the given cells, substituting *fallback* where unusable.

    Used for the per-site dilution factor, which is the rainfall-weighted
    contributing area at the snapped outlet. A missing or non-positive value
    there would divide the whole site's predictor by zero or flip its sign, so
    it falls back rather than propagating.
    """
    array = np.asarray(array, dtype=np.float64)
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)

    inside = (
        (rows >= 0) & (rows < array.shape[0])
        & (cols >= 0) & (cols < array.shape[1])
    )
    sampled = np.full(rows.shape, np.nan, dtype=np.float64)
    if np.any(inside):
        sampled[inside] = array[rows[inside], cols[inside]]

    return np.where(np.isfinite(sampled) & (sampled > 0), sampled, float(fallback))
