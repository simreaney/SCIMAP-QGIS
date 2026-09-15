"""Windowed (chunked) SCIMAP-Flood accumulation.

Mirrors ``core.py``'s streaming ``SCIMAPFlood`` implementation using GDAL bands
so large catchments are processed one tile at a time instead of holding every
input raster in memory at once.
"""

import os
import tempfile

import numpy as np

from ._numba import njit, prange


def _clean_flood_input_array(arr, nodata):
    """Apply nodata → NaN and negative → NaN, matching core.py's read_window.

    SCIMAP-Flood treats negative values as invalid on every input
    raster (connectivity, runoff, rainfall, overland flow distance), not
    just the raster's declared nodata value.
    """
    arr = arr.astype(np.float32, copy=False)
    if nodata is not None:
        if np.isfinite(nodata):
            arr = np.where(np.isclose(arr, nodata), np.nan, arr)
        else:
            arr = np.where(arr == nodata, np.nan, arr)
    arr = np.where(arr < 0, np.nan, arr)
    return arr.astype(np.float32, copy=False)


# SCIMAP-Flood combination loop: normalize + accumulate.
#
# These were previously plain single-threaded NumPy (no Numba at all,
# unlike core.py's equivalents), and the per-combination accumulation
# built several full-raster temporary arrays (product, valid_combo,
# product**2, two np.where results) on every one of the
# len(rainfall) x len(overland) combinations. For a large raster with
# several combinations that's a lot of redundant memory traffic and
# zero use of extra cores. The Numba kernels below do the same math in
# a single fused pass per array, parallelised across rows with prange,
# with a NaN-propagating (non-Numba) NumPy fallback if Numba is
# unavailable.
if njit is not None:
    @njit(parallel=True)
    def _normalize_rainfall_array(data, minimum, maximum):
        rows, cols = data.shape
        out = np.empty((rows, cols), dtype=np.float32)
        scale = maximum - minimum
        for i in prange(rows):
            for j in range(cols):
                v = data[i, j]
                if v == v:
                    out[i, j] = (v - minimum) / scale
                else:
                    out[i, j] = np.nan
        return out

    @njit(parallel=True)
    def _normalize_overland_flow_array(data, minimum, median, maximum):
        rows, cols = data.shape
        out = np.empty((rows, cols), dtype=np.float32)
        left_scale = median - minimum
        right_scale = maximum - median
        for i in prange(rows):
            for j in range(cols):
                v = data[i, j]
                if v != v:
                    out[i, j] = np.nan
                    continue
                if v <= median:
                    r = (v - minimum) / left_scale if left_scale > 0.0 else 1.0
                else:
                    r = (maximum - v) / right_scale if right_scale > 0.0 else 1.0
                if r < 0.0:
                    r = 0.0
                elif r > 1.0:
                    r = 1.0
                out[i, j] = r
        return out

    @njit(parallel=True)
    def _accumulate_flood_combo_kernel(base, rainfall_norm, overland_norm, sum_term, sumsq_term, combination_count):
        """Fuse product = base*rainfall*overland with the running mean/stdev
        accumulators in one parallel pass, avoiding per-combination temporaries."""
        rows, cols = base.shape
        for i in prange(rows):
            for j in range(cols):
                b = base[i, j]
                r = rainfall_norm[i, j]
                o = overland_norm[i, j]
                if b == b and r == r and o == o:
                    p = b * r * o
                    sum_term[i, j] += p
                    sumsq_term[i, j] += p * p
                    combination_count[i, j] += 1
else:
    def _normalize_rainfall_array(data, minimum, maximum):
        out = np.full_like(data, np.nan, dtype=np.float32)
        finite = np.isfinite(data)
        out[finite] = (data[finite] - minimum) / (maximum - minimum)
        return out

    def _normalize_overland_flow_array(data, minimum, median, maximum):
        out = np.full_like(data, np.nan, dtype=np.float32)
        finite = np.isfinite(data)

        left_mask = finite & (data <= median)
        left_scale = median - minimum
        if left_scale > 0:
            out[left_mask] = (data[left_mask] - minimum) / left_scale
        else:
            out[left_mask] = 1.0

        right_mask = finite & (data > median)
        right_scale = maximum - median
        if right_scale > 0:
            out[right_mask] = (maximum - data[right_mask]) / right_scale
        else:
            out[right_mask] = 1.0

        out = np.where(np.isfinite(out), np.clip(out, 0.0, 1.0), out)
        return out.astype(np.float32, copy=False)

    def _accumulate_flood_combo_kernel(base, rainfall_norm, overland_norm, sum_term, sumsq_term, combination_count):
        valid_combo = np.isfinite(base) & np.isfinite(rainfall_norm) & np.isfinite(overland_norm)
        product = base * rainfall_norm * overland_norm
        sum_term += np.where(valid_combo, product, 0.0).astype(np.float32, copy=False)
        sumsq_term += np.where(valid_combo, product * product, 0.0).astype(np.float32, copy=False)
        combination_count += valid_combo.astype(np.uint32)


# SCIMAP-Flood windowed (chunked) processing.
#
# ScimapFloodAlgorithm used to ReadAsArray() every input raster (connectivity,
# runoff, every rainfall map, every OFD map) in full and hold them all in
# memory simultaneously, then build several more full-raster temporaries per
# combination on top of that. For large catchments that is a lot of RAM for
# what is, per pixel, a purely local calculation. These helpers mirror
# core.py's rasterio-based windowed approach (iter_windows/read_window/
# stream_raster_min_max/stream_raster_median) using GDAL bands instead, so
# the plugin can process one chunk_size x chunk_size tile at a time.
FLOOD_DEFAULT_CHUNK_SIZE = 1024


def _flood_iter_windows(width, height, chunk_size=FLOOD_DEFAULT_CHUNK_SIZE):
    for row_off in range(0, height, chunk_size):
        window_height = min(chunk_size, height - row_off)
        for col_off in range(0, width, chunk_size):
            window_width = min(chunk_size, width - col_off)
            yield (col_off, row_off, window_width, window_height)


def _flood_read_window(band, window, nodata):
    col_off, row_off, win_width, win_height = window
    data = band.ReadAsArray(col_off, row_off, win_width, win_height)
    data = np.asarray(data, dtype=np.float32)
    return _clean_flood_input_array(data, nodata)


def _flood_stream_min_max(band, nodata, width, height, chunk_size=FLOOD_DEFAULT_CHUNK_SIZE):
    minimum = np.inf
    maximum = -np.inf
    for window in _flood_iter_windows(width, height, chunk_size):
        data = _flood_read_window(band, window, nodata)
        finite = data[np.isfinite(data)]
        if finite.size:
            window_min = float(finite.min())
            window_max = float(finite.max())
            if window_min < minimum:
                minimum = window_min
            if window_max > maximum:
                maximum = window_max
    return minimum, maximum


def _flood_stream_median(band, nodata, width, height, chunk_size=FLOOD_DEFAULT_CHUNK_SIZE):
    valid_count = 0
    for window in _flood_iter_windows(width, height, chunk_size):
        data = _flood_read_window(band, window, nodata)
        valid_count += int(np.isfinite(data).sum())

    if valid_count == 0:
        raise RuntimeError("No valid values found while computing OFD median")

    temp_file = tempfile.NamedTemporaryFile(prefix="scimap_flood_median_", suffix=".bin", delete=False)
    temp_file.close()
    try:
        values = np.memmap(temp_file.name, dtype=np.float32, mode="w+", shape=(valid_count,))
        offset = 0
        for window in _flood_iter_windows(width, height, chunk_size):
            data = _flood_read_window(band, window, nodata)
            valid = data[np.isfinite(data)]
            next_offset = offset + valid.size
            values[offset:next_offset] = valid
            offset = next_offset
        median = float(np.median(values))
        del values
    finally:
        try:
            os.unlink(temp_file.name)
        except OSError:
            pass
    return median


def _run_scimap_flood_windowed(
    conn_band, conn_nodata,
    runoff_band, runoff_nodata,
    rainfall_bands, rainfall_nodata, rainfall_min_max,
    ofd_bands, ofd_nodata, ofd_stats,
    width, height,
    write_mean_window, write_stdev_window,
    chunk_size=FLOOD_DEFAULT_CHUNK_SIZE,
    progress_callback=None,
):
    """Chunked SCIMAP-Flood mean/stdev pass.

    Reads every input one window at a time, accumulates mean/stdev/count
    for that window only, and hands the (unnormalised) mean and stdev
    windows off to the caller's write callbacks. Returns the global
    maximum of the (unnormalised) mean, which the caller needs for a
    second windowed pass to normalise the mean output — mirroring
    core.py's SCIMAPFlood.go()/save_results() split.
    """
    windows = list(_flood_iter_windows(width, height, chunk_size))
    n_windows = len(windows)
    mean_max = -np.inf

    for w_idx, window in enumerate(windows):
        conn_window = _flood_read_window(conn_band, window, conn_nodata)
        runoff_window = _flood_read_window(runoff_band, window, runoff_nodata)
        base = (conn_window * runoff_window).astype(np.float32, copy=False)

        shape = base.shape
        combination_count = np.zeros(shape, dtype=np.uint32)
        sum_term = np.zeros(shape, dtype=np.float32)
        sumsq_term = np.zeros(shape, dtype=np.float32)

        rainfall_norm_windows = [
            _normalize_rainfall_array(
                _flood_read_window(band, window, rainfall_nodata[idx]),
                *rainfall_min_max[idx],
            )
            for idx, band in enumerate(rainfall_bands)
        ]

        for ofd_idx, ofd_band in enumerate(ofd_bands):
            ofd_min, ofd_median, ofd_max = ofd_stats[ofd_idx]
            ofd_window = _flood_read_window(ofd_band, window, ofd_nodata[ofd_idx])
            overland_norm = _normalize_overland_flow_array(ofd_window, ofd_min, ofd_median, ofd_max)

            for rainfall_norm in rainfall_norm_windows:
                _accumulate_flood_combo_kernel(
                    base, rainfall_norm, overland_norm,
                    sum_term, sumsq_term, combination_count,
                )

        valid = combination_count > 0

        mean_window = np.full(shape, np.nan, dtype=np.float32)
        np.divide(sum_term, combination_count, out=mean_window, where=valid)

        finite_mean = mean_window[np.isfinite(mean_window)]
        if finite_mean.size:
            window_max = float(finite_mean.max())
            if window_max > mean_max:
                mean_max = window_max

        second_moment = np.full(shape, np.nan, dtype=np.float32)
        np.divide(sumsq_term, combination_count, out=second_moment, where=valid)
        variance = np.where(valid, second_moment - (mean_window * mean_window), np.nan).astype(np.float32, copy=False)
        variance = np.where(np.isfinite(variance), np.maximum(variance, 0.0), np.nan).astype(np.float32, copy=False)
        stdev_window = np.sqrt(variance).astype(np.float32, copy=False)

        write_mean_window(window, mean_window)
        write_stdev_window(window, stdev_window)

        if progress_callback is not None:
            progress_callback(w_idx + 1, n_windows)

    if not np.isfinite(mean_max) or mean_max == 0:
        raise RuntimeError("Invalid maximum while normalising mean output.")

    return mean_max
