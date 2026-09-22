"""Blocked combination maths for the SCIMAP-Fitted ensemble maps.

Ported from ``SCIMAP-Fitted-Step4.py``.

The ensemble stage turns N calibrated weight sets into summary rasters. Done
naively that means routing the risk raster down the flow network once per
weight set, which for thirty sets is thirty full-grid mass-flux runs at
ten-odd minutes each.

It exploits linearity instead. For a fixed connectivity and land-cover pair the
local risk of a cell is ``connectivity x erosion x weight[class of cell]``,
which is a weighted sum of per-class indicator rasters; flow routing is linear,
so the routed result for any weight set is the same weighted sum of the
per-class *routed* rasters. Route once per land-cover class — seven runs, not
thirty — then recombine arithmetically.

The recombination is still (n_combinations x full grid), which does not fit in
memory on a large raster, so everything here works in row blocks and streams
its outputs to disk.

**Threading contract.** ``progress`` and ``cancel`` callables are invoked only
from the calling thread, never a worker, because in QGIS they touch dialog
widgets. Worker threads must open their own GDAL datasets: a dataset handle is
not thread-safe, and sharing one is an intermittent segfault rather than an
exception.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

#: Bytes the working set of one row block should stay under, per worker.
BLOCK_TARGET_BYTES = 1.5e9

#: Cells sampled when estimating a combination's high-risk threshold.
NO_REGRETS_SAMPLE_CAP = 2_000_000


def block_rows_for(width, n_combinations, budget_bytes=BLOCK_TARGET_BYTES,
                   workers=1, height=None, min_blocks=50):
    """Rows per block so the in-flight stack fits the memory budget.

    The array being governed is ``(n_combinations, block_rows, width)`` in
    float32, with *workers* of them alive at once. *min_blocks* additionally
    caps the block height so progress is reported and cancellation is noticed
    often enough for the dialog to stay responsive — a single enormous block
    would meet the budget and then appear to hang.
    """
    per_worker = budget_bytes / max(1, workers)
    by_memory = int(per_worker // max(1, width * n_combinations * 4))
    candidates = [by_memory]
    if height:
        candidates.append(height // max(1, min_blocks))
    return max(1, min(value for value in candidates if value is not None))


def iter_row_blocks(height, block_rows):
    """Yield ``(row_start, row_end)`` covering *height*."""
    for start in range(0, height, block_rows):
        yield start, min(start + block_rows, height)


def nan_median_iqr(stacked, axis=0, copy=False):
    """Median and inter-quartile range along *axis*, ignoring NaN.

    ``np.nanmedian``/``np.nanpercentile`` are pathologically slow when the NaN
    count varies from cell to cell, which it always does here — catchment edges
    and undefined connectivity leave different holes in different combinations.
    This sorts once (NaN sorts to the end) and derives each cell's quantile
    position from its own valid count.

    **Precondition:** with ``copy=False`` the sort happens in place and
    *stacked* is destroyed. Every caller here passes a freshly built temporary
    and computes mean/stdev first; a full copy would double the peak memory
    this module exists to bound. Pass ``copy=True`` to keep the input.
    """
    array = np.array(stacked, copy=True) if copy else np.asarray(stacked)
    if axis != 0:
        array = np.moveaxis(array, axis, 0)

    valid = np.isfinite(array).sum(axis=0)
    array.sort(axis=0)
    depth = array.shape[0]

    def quantile(fraction):
        position = (valid - 1).astype(np.float64) * fraction
        lower = np.clip(np.floor(position), 0, depth - 1).astype(np.intp)
        upper = np.clip(np.ceil(position), 0, depth - 1).astype(np.intp)
        weight = position - lower
        low = np.take_along_axis(array, lower[np.newaxis, ...], axis=0)[0]
        high = np.take_along_axis(array, upper[np.newaxis, ...], axis=0)[0]
        return low + (high - low) * weight

    has_data = valid > 0
    median = np.where(has_data, quantile(0.5), np.nan)
    spread = np.where(has_data, quantile(0.75) - quantile(0.25), np.nan)
    return median, spread


def build_weight_lookup(weights, classes):
    """A ``(n_sets, max_class + 1)`` table for indexing by land-cover class.

    Land cover is one-hot per cell, so weighting collapses to a lookup:
    ``lut[:, class_of_cell]``. Classes absent from *classes* map to NaN so an
    invalid or unmapped land-cover value propagates as NoData rather than
    silently contributing zero risk.
    """
    weights = np.asarray(weights, dtype=np.float32)
    classes = [int(c) for c in classes]
    lookup = np.full((weights.shape[0], max(classes) + 1), np.nan, dtype=np.float32)
    for index, class_id in enumerate(classes):
        lookup[:, class_id] = weights[:, index]
    return lookup


def _check(cancel):
    if cancel is not None:
        cancel()


def combination_stats(read_block, height, width, n_combinations, block_rows,
                      write_mean=None, write_stdev=None, write_median=None,
                      write_iqr=None, workers=1, progress=None, cancel=None,
                      reduce_block=None, on_reduced=None):
    """Summarise an ensemble across combinations, one row block at a time.

    *read_block* is ``(row_start, row_end) -> (n_combinations, rows, width)``
    and is called on worker threads, so it must open its own datasets. The
    ``write_*`` callables take ``(block, row_start)`` and are called only on the
    calling thread, in block order per completion — GDAL writes must not race.

    *reduce_block* ``(stacked, row_start) -> partial`` lets a caller take its
    own summary of the raw ensemble while it is already in memory; it runs on
    the worker, before the in-place sort, and so must not touch feedback. Each
    partial is handed to *on_reduced* on the calling thread. This is how the
    per-combination river-network summary is produced without a second full
    traversal of the grid.

    Returns the maximum finite mean seen, which callers use to scale styling.
    """
    blocks = list(iter_row_blocks(height, block_rows))
    peak = -np.inf
    done = 0

    def compute(row_start, row_end):
        stacked = read_block(row_start, row_end)
        partial = reduce_block(stacked, row_start) if reduce_block else None
        with np.errstate(invalid='ignore', divide='ignore'):
            mean = np.nanmean(stacked, axis=0)
            stdev = np.nanstd(stacked, axis=0)
        # After mean/stdev and after reduce_block, so the in-place sort is safe.
        median, spread = nan_median_iqr(stacked, copy=False)
        return mean, stdev, median, spread, partial

    def emit(row_start, payload):
        nonlocal peak
        mean, stdev, median, spread, partial = payload
        if on_reduced is not None and partial is not None:
            on_reduced(partial)
        if write_mean is not None:
            write_mean(mean, row_start)
        if write_stdev is not None:
            write_stdev(stdev, row_start)
        if write_median is not None:
            write_median(median, row_start)
        if write_iqr is not None:
            write_iqr(spread, row_start)
        finite = mean[np.isfinite(mean)]
        if finite.size:
            peak = max(peak, float(finite.max()))

    if workers > 1 and len(blocks) > 1:
        with ThreadPoolExecutor(max_workers=int(workers)) as executor:
            futures = {
                executor.submit(compute, start, end): start
                for start, end in blocks
            }
            for future in as_completed(futures):
                emit(futures[future], future.result())
                done += 1
                _check(cancel)
                if progress is not None:
                    progress(done / len(blocks))
    else:
        for start, end in blocks:
            emit(start, compute(start, end))
            done += 1
            _check(cancel)
            if progress is not None:
                progress(done / len(blocks))

    return peak if np.isfinite(peak) else float('nan')


def no_regrets_thresholds(read_block, height, width, n_combinations, block_rows,
                          top_percent, max_samples=NO_REGRETS_SAMPLE_CAP,
                          progress=None, cancel=None):
    """Each combination's cut-off for its own top *top_percent* of cells.

    Estimated from a strided sample rather than the whole grid. The stride is
    aligned to the *global* flat index, not restarted per block, so the sample
    is the same regardless of how the grid happens to be blocked.
    """
    total_cells = height * width
    stride = max(1, int(np.ceil(total_cells / max(1, max_samples))))
    samples = [[] for _ in range(n_combinations)]
    blocks = list(iter_row_blocks(height, block_rows))

    for index, (row_start, row_end) in enumerate(blocks, 1):
        stacked = read_block(row_start, row_end)
        flat = stacked.reshape(n_combinations, -1)
        offset = (-(row_start * width)) % stride
        taken = flat[:, offset::stride]
        for combination in range(n_combinations):
            values = taken[combination]
            samples[combination].append(values[np.isfinite(values)])
        _check(cancel)
        if progress is not None:
            progress(index / len(blocks))

    thresholds = np.full(n_combinations, np.nan, dtype=np.float64)
    for combination in range(n_combinations):
        pooled = np.concatenate(samples[combination]) if samples[combination] else np.array([])
        if pooled.size:
            thresholds[combination] = np.percentile(pooled, 100.0 - top_percent)
    return thresholds


def no_regrets_percentage(read_block, height, width, n_combinations, block_rows,
                          thresholds, write_block, progress=None, cancel=None):
    """Percentage of combinations for which each cell is in their top tier.

    A cell scoring 100 is high-priority whichever calibrated weight set turns
    out to be right — which is a far more actionable statement than any single
    weight set's map, and is the point of carrying an ensemble at all.
    """
    thresholds = np.asarray(thresholds, dtype=np.float64)[:, np.newaxis, np.newaxis]
    blocks = list(iter_row_blocks(height, block_rows))

    for index, (row_start, row_end) in enumerate(blocks, 1):
        stacked = read_block(row_start, row_end)
        finite = np.isfinite(stacked)
        with np.errstate(invalid='ignore'):
            exceeds = finite & (stacked >= thresholds)
        counted = finite.sum(axis=0)
        percentage = np.where(
            counted > 0,
            exceeds.sum(axis=0) / np.maximum(counted, 1) * 100.0,
            np.nan,
        )
        write_block(percentage.astype(np.float32, copy=False), row_start)
        _check(cancel)
        if progress is not None:
            progress(index / len(blocks))
