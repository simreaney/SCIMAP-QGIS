"""Summary statistics for the web dashboard.

Pure Python and NumPy, no GDAL and no QGIS, so the arithmetic behind the
numbers people quote in a report can be tested in a plain environment. The
callers in :mod:`webexport` do the raster reading and hand the counts here.
"""

import bisect


def lorenz_from_histogram(counts, edge, width, points=320):
    """A Lorenz curve of risk over land, from a histogram of cell values.

    *counts* is the number of cells in each equal-width bucket, *edge* the
    value at the low edge of the first bucket and *width* the bucket width.
    Cells are ranked lowest risk to highest; the curve gives the share of total
    risk held by each share of land.

    Cells sharing a bucket are split proportionally rather than the whole
    bucket being taken at once. Without that, a skewed risk surface — most of
    the catchment near zero, which is the normal case — puts a quantile deep
    inside a single bucket and the answer comes out several points wrong.

    Negative values cannot hold a share of a positive total, so they are
    counted as land carrying no risk. Returns ``None`` when there is no risk to
    apportion.
    """
    cum_cells, cum_load = [0.0], [0.0]
    for i, count in enumerate(counts):
        centre = max(0.0, edge + width * (i + 0.5))
        cum_cells.append(cum_cells[-1] + count)
        cum_load.append(cum_load[-1] + count * centre)

    total_cells = cum_cells[-1]
    total_load = cum_load[-1]
    if total_cells <= 0 or total_load <= 0:
        return None

    def _interpolate(cumulative, other, value):
        if value <= 0:
            return other[0]
        if value >= cumulative[-1]:
            return other[-1]
        i = bisect.bisect_right(cumulative, value) - 1
        i = min(max(i, 0), len(cumulative) - 2)
        span = cumulative[i + 1] - cumulative[i]
        if span <= 0:
            return other[i]
        return other[i] + (value - cumulative[i]) / span * (other[i + 1] - other[i])

    def load_below(cells):
        """Risk load held by the *cells* lowest-risk cells."""
        return _interpolate(cum_cells, cum_load, cells)

    def cells_below(load):
        """Cells needed, lowest risk first, to accumulate *load*."""
        return _interpolate(cum_load, cum_cells, load)

    def risk_for_worst(land_fraction):
        """Share of total risk on the highest-risk *land_fraction* of land."""
        return 1.0 - load_below(total_cells * (1.0 - land_fraction)) / total_load

    def land_for_risk(risk_fraction):
        """Share of land, highest risk first, needed to reach *risk_fraction*."""
        return 1.0 - cells_below(total_load * (1.0 - risk_fraction)) / total_cells

    xs = [round(i / float(points), 6) for i in range(points + 1)]
    ys = [round(load_below(total_cells * x) / total_load, 6) for x in xs]

    top5 = risk_for_worst(0.05) * 100.0
    return {
        'x': xs,
        'y': ys,
        'worst': [{'landPct': pct,
                   'riskPct': round(100.0 * risk_for_worst(pct / 100.0), 1)}
                  for pct in (1, 5, 10, 25, 50)],
        'needed': [{'riskPct': pct,
                    'landPct': round(100.0 * land_for_risk(pct / 100.0), 1)}
                   for pct in (50, 80, 95)],
        'headline': (f"The highest-risk 5% of the catchment produces {top5:.0f}% "
                     f"of the total risk."),
        'cells': int(total_cells),
        'gini': round(gini(xs, ys), 3),
    }


def gini(xs, ys):
    """Gini coefficient: twice the area between the curve and the 1:1 line.

    0 is risk spread perfectly evenly over the land, 1 is all of it in one
    place. Trapezoidal, which is exact for the straight segments the curve is
    already made of.
    """
    area = 0.0
    for i in range(1, len(xs)):
        area += (xs[i] - xs[i - 1]) * (ys[i] + ys[i - 1]) / 2.0
    return max(0.0, min(1.0, 1.0 - 2.0 * area))
