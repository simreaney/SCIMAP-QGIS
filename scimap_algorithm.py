import logging
import math
import os
import numpy as np
import tempfile
import gc
from osgeo import gdal, ogr, osr

try:
    from processing.wb_utils import compute_connectivity_flow_path_trace as shared_compute_connectivity_flow_path_trace
except ModuleNotFoundError:
    shared_compute_connectivity_flow_path_trace = None

try:
    from processing.wb_utils import create_pour_point_raster, wbt_cost_distance
except ModuleNotFoundError:
    create_pour_point_raster = None
    wbt_cost_distance = None

try:
    from numba import njit, prange
except ModuleNotFoundError:
    njit = None
    prange = range

# Maps WBT D8 pointer value -> (dcol, drow) in array coordinates
WBT_D8 = {
    1:   ( 1,  0),  # E
    2:   ( 1, -1),  # NE
    4:   ( 0, -1),  # N
    8:   (-1, -1),  # NW
    16:  (-1,  0),  # W
    32:  (-1,  1),  # SW
    64:  ( 0,  1),  # S
    128: ( 1,  1),  # SE
}

import subprocess
import shutil
from qgis.core import (
    QgsApplication,
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsMessageLog,
    QgsSettings,
    QgsProcessingLayerPostProcessorInterface,
    QgsRasterLayer,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
    QgsSpatialIndex,
    QgsPointXY,
    Qgis
)
import processing # Built-in QGIS Processing wrapper
from .localization import tr


logger = logging.getLogger(__name__)


def _create_pour_point_raster_local(x, y, template_path, output_path):
    """Create a single-cell pour-point raster aligned to *template_path*."""
    ds_t = gdal.Open(template_path, gdal.GA_ReadOnly)
    if ds_t is None:
        raise RuntimeError(f"Could not open template raster for pour point: {template_path}")

    gt = ds_t.GetGeoTransform()
    proj = ds_t.GetProjection()
    cols = ds_t.RasterXSize
    rows = ds_t.RasterYSize
    ds_t = None

    col = int((x - gt[0]) / gt[1])
    row = int((y - gt[3]) / gt[5])
    col = max(0, min(col, cols - 1))
    row = max(0, min(row, rows - 1))

    arr = np.zeros((rows, cols), dtype=np.int8)
    arr[row, col] = 1

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, cols, rows, 1, gdal.GDT_Byte)
    if ds is None:
        raise RuntimeError(f"Could not create pour point raster: {output_path}")
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def _create_points_raster_local(points, template_path, output_path):
    """Create a raster with one marked source cell per (x, y) in *points*,
    aligned to *template_path*. WhiteboxTools CostDistance treats every
    non-zero cell in the source raster as a target, so multiple points
    yield the distance to whichever point is nearest."""
    ds_t = gdal.Open(template_path, gdal.GA_ReadOnly)
    if ds_t is None:
        raise RuntimeError(f"Could not open template raster for points: {template_path}")

    gt = ds_t.GetGeoTransform()
    proj = ds_t.GetProjection()
    cols = ds_t.RasterXSize
    rows = ds_t.RasterYSize
    ds_t = None

    arr = np.zeros((rows, cols), dtype=np.int8)
    for x, y in points:
        col = int((x - gt[0]) / gt[1])
        row = int((y - gt[3]) / gt[5])
        col = max(0, min(col, cols - 1))
        row = max(0, min(row, rows - 1))
        arr[row, col] = 1

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, cols, rows, 1, gdal.GDT_Byte)
    if ds is None:
        raise RuntimeError(f"Could not create points raster: {output_path}")
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def _run_wbt_cost_distance_local(run_wbt_func, source_path, cost_path, output_path):
    """Run WhiteboxTools CostDistance directly through plugin's WBT launcher."""
    backlink_path = os.path.join(
        os.path.dirname(output_path),
        f"{os.path.splitext(os.path.basename(output_path))[0]}_backlink.tif",
    )
    run_wbt_func("CostDistance", {
        'source': source_path,
        'cost': cost_path,
        'out_accum': output_path,
        'out_backlink': backlink_path,
    })
    return backlink_path


def _compute_connectivity_flow_path_trace_local(
    d8_array,
    twi_array,
    mask_array=None,
    channel_mask=None,
    dem_array=None,
    progress_callback=None,
):
    if progress_callback is not None:
        progress_callback(46, "5.1 Preparing connectivity mask...")

    rows, cols = twi_array.shape

    if mask_array is not None:
        valid = np.isfinite(twi_array) & (mask_array == 1)
    else:
        valid = np.isfinite(twi_array)

    twi_flat = twi_array.astype(np.float64, copy=False).ravel()
    valid_flat = valid.astype(np.bool_, copy=False).ravel()
    if channel_mask is not None:
        terminal_flat = (np.asarray(channel_mask, dtype=bool) & valid).ravel().astype(np.bool_, copy=False)
    else:
        terminal_flat = np.zeros(twi_flat.size, dtype=np.bool_)

    if progress_callback is not None:
        progress_callback(48, "5.2 Building downstream flow index...")

    if dem_array is not None:
        downstream_flat = _build_downstream_index_from_dem_local(
            dem_array.astype(np.float64, copy=False),
            valid,
        )
    else:
        downstream_flat = _build_downstream_index_from_d8_local(
            d8_array.astype(np.int32, copy=False),
            valid,
        )

    if progress_callback is not None:
        progress_callback(51, "5.3 Tracing flow paths to channel network... 0%")

    conn_arr = _flow_path_trace_pointer_doubling(
        downstream_flat,
        twi_flat,
        valid_flat,
        terminal_flat,
        rows,
        cols,
        progress_callback,
    )

    # Normalise to [0, 1] using 5th/95th percentile
    if progress_callback is not None:
        progress_callback(55, "5.4 Normalising connectivity scores...")

    valid_vals = conn_arr[valid]
    if valid_vals.size:
        c5, c95 = np.nanpercentile(valid_vals, [5, 95])
        if c95 <= c5:
            c95 = c5 + 1.0
        conn_arr = np.clip((conn_arr - c5) / (c95 - c5), 0.0, 1.0)
        conn_arr[~valid] = np.nan

    if progress_callback is not None:
        progress_callback(57, "5.5 Connectivity computation complete.")

    return conn_arr


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


def _build_downstream_index_from_d8_local(d8_array, valid_mask):
    rows, cols = d8_array.shape
    d8_flat = d8_array.ravel().astype(np.int32)
    downstream = np.full(rows * cols, -1, dtype=np.int64)

    for d_val, (dc, dr) in WBT_D8.items():
        fi = np.where(d8_flat == d_val)[0]
        if fi.size == 0:
            continue
        rs, cs = fi // cols, fi % cols
        nr, nc = rs + dr, cs + dc
        in_bounds = (nr >= 0) & (nr < rows) & (nc >= 0) & (nc < cols)
        fi_ib = fi[in_bounds]
        nflat = nr[in_bounds] * cols + nc[in_bounds]
        ds_ok = valid_mask.ravel()[nflat]
        downstream[fi_ib[ds_ok]] = nflat[ds_ok]

    return downstream


if njit is not None:
    @njit(cache=True, parallel=True)
    def _build_downstream_index_from_dem_kernel(
        dem_flat,
        valid_flat,
        rows,
        cols,
    ):
        rows_i = np.int64(rows)
        cols_i = np.int64(cols)
        n_cells = np.int64(dem_flat.size)
        downstream = np.full(n_cells, -1, dtype=np.int64)
        sqrt2 = np.sqrt(2.0)

        for fi in prange(n_cells):
            fi_i = np.int64(fi)
            if not valid_flat[fi_i]:
                continue

            cr = fi_i // cols_i
            cc = fi_i % cols_i
            z0 = dem_flat[fi_i]
            best_grad = 0.0
            best_idx = np.int64(-1)

            for k in range(8):
                if k == 0:
                    dr = -1
                    dc = 0
                    dist = 1.0
                elif k == 1:
                    dr = -1
                    dc = 1
                    dist = sqrt2
                elif k == 2:
                    dr = 0
                    dc = 1
                    dist = 1.0
                elif k == 3:
                    dr = 1
                    dc = 1
                    dist = sqrt2
                elif k == 4:
                    dr = 1
                    dc = 0
                    dist = 1.0
                elif k == 5:
                    dr = 1
                    dc = -1
                    dist = sqrt2
                elif k == 6:
                    dr = 0
                    dc = -1
                    dist = 1.0
                else:
                    dr = -1
                    dc = -1
                    dist = sqrt2

                nr = cr + dr
                nc = cc + dc
                if nr < 0 or nr >= rows_i or nc < 0 or nc >= cols_i:
                    continue

                idx_next = nr * cols_i + nc
                if not valid_flat[idx_next]:
                    continue

                grad = (z0 - dem_flat[idx_next]) / dist
                if grad > best_grad:
                    best_grad = grad
                    best_idx = idx_next

            downstream[fi_i] = best_idx

        return downstream

    def _build_downstream_index_from_dem_local(dem_array, valid_mask):
        rows, cols = dem_array.shape
        return _build_downstream_index_from_dem_kernel(
            dem_array.astype(np.float64, copy=False).ravel(),
            np.asarray(valid_mask, dtype=bool).ravel(),
            rows,
            cols,
        )

else:
    def _build_downstream_index_from_dem_local(dem_array, valid_mask):
        rows, cols = dem_array.shape
        valid_flat = np.asarray(valid_mask, dtype=bool).ravel()
        dem_flat = dem_array.astype(np.float64, copy=False).ravel()
        downstream = np.full(rows * cols, -1, dtype=np.int64)
        sqrt2 = np.sqrt(2.0)

        for fi in range(rows * cols):
            if not valid_flat[fi]:
                continue

            cr = fi // cols
            cc = fi % cols
            z0 = dem_flat[fi]
            best_grad = 0.0
            best_idx = -1

            for dr, dc, dist in (
                (-1, 0, 1.0),
                (-1, 1, sqrt2),
                (0, 1, 1.0),
                (1, 1, sqrt2),
                (1, 0, 1.0),
                (1, -1, sqrt2),
                (0, -1, 1.0),
                (-1, -1, sqrt2),
            ):
                nr = cr + dr
                nc = cc + dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    continue
                idx_next = nr * cols + nc
                if not valid_flat[idx_next]:
                    continue
                grad = (z0 - dem_flat[idx_next]) / dist
                if grad > best_grad:
                    best_grad = grad
                    best_idx = idx_next

            downstream[fi] = best_idx

        return downstream


# Flow-path connectivity: pointer doubling (binary lifting) over the
# downstream D8/DEM successor map.
#
# The original implementation walked the full downstream chain
# independently for every one of the N cells, giving O(N * L) work where
# L is the average flow-path length to the channel network (often
# hundreds to thousands of cells for real catchments) — almost all of
# that work is redundant, since neighbouring cells share most of their
# downstream path. Pointer doubling instead advances every cell's
# "successor" pointer by 2x each round (1, 2, 4, 8, ... steps), folding
# in the running min-TWI as it goes, so the whole raster converges in
# O(log L) rounds of O(N) parallel work — typically 15-25 rounds
# regardless of how long the flow paths are, with every round using all
# available cores via prange.
#
# Recurrence being solved, per valid cell x with successor d(x):
#   f(x) = twi(x)                          if d(x) is absent
#   f(x) = min(twi(x), twi(d(x)))          if d(x) is a channel/terminal cell
#   f(x) = min(twi(x), f(d(x)))            otherwise
if njit is not None:
    @njit(cache=True, parallel=True)
    def _flow_path_trace_init_kernel(downstream_flat, twi_flat, valid_flat, terminal_flat):
        n_cells = np.int64(twi_flat.size)
        val = np.full(n_cells, np.nan, dtype=np.float64)
        nxt = np.full(n_cells, -1, dtype=np.int64)
        done = np.zeros(n_cells, dtype=np.bool_)

        for fi in prange(n_cells):
            fi_i = np.int64(fi)
            if not valid_flat[fi_i]:
                continue

            v = twi_flat[fi_i]
            d = downstream_flat[fi_i]
            if d < 0:
                val[fi_i] = v
                done[fi_i] = True
            elif terminal_flat[d]:
                dv = twi_flat[d]
                if dv == dv and dv < v:
                    v = dv
                val[fi_i] = v
                done[fi_i] = True
            else:
                val[fi_i] = v
                nxt[fi_i] = d
                done[fi_i] = False

        return val, nxt, done

    @njit(cache=True, parallel=True)
    def _flow_path_trace_round_kernel(val, nxt, done):
        n_cells = np.int64(val.size)
        new_val = val.copy()
        new_nxt = nxt.copy()
        new_done = done.copy()

        for fi in prange(n_cells):
            fi_i = np.int64(fi)
            if done[fi_i]:
                continue

            j = nxt[fi_i]
            if j < 0:
                new_done[fi_i] = True
                continue

            v = val[fi_i]
            vj = val[j]
            if vj == vj and vj < v:
                v = vj

            new_val[fi_i] = v
            new_nxt[fi_i] = nxt[j]
            new_done[fi_i] = done[j]

        return new_val, new_nxt, new_done
else:
    def _flow_path_trace_init_kernel(downstream_flat, twi_flat, valid_flat, terminal_flat):
        n_cells = twi_flat.size
        val = np.full(n_cells, np.nan, dtype=np.float64)
        nxt = np.full(n_cells, -1, dtype=np.int64)
        done = np.zeros(n_cells, dtype=np.bool_)

        valid_idx = np.where(valid_flat)[0]
        d = downstream_flat[valid_idx]
        v = twi_flat[valid_idx]

        no_ds = d < 0
        val[valid_idx[no_ds]] = v[no_ds]
        done[valid_idx[no_ds]] = True

        has_ds = ~no_ds
        idx_hd = valid_idx[has_ds]
        d_hd = d[has_ds]
        v_hd = v[has_ds]
        term_next = terminal_flat[d_hd]

        idx_term = idx_hd[term_next]
        d_term = d_hd[term_next]
        v_term = v_hd[term_next]
        dv = twi_flat[d_term]
        best = np.where(np.isfinite(dv) & (dv < v_term), dv, v_term)
        val[idx_term] = best
        done[idx_term] = True

        idx_cont = idx_hd[~term_next]
        val[idx_cont] = v_hd[~term_next]
        nxt[idx_cont] = d_hd[~term_next]
        done[idx_cont] = False

        return val, nxt, done

    def _flow_path_trace_round_kernel(val, nxt, done):
        new_val = val.copy()
        new_nxt = nxt.copy()
        new_done = done.copy()

        active_idx = np.where(~done)[0]
        if active_idx.size:
            j = nxt[active_idx]
            has_next = j >= 0

            idx_a = active_idx[has_next]
            j_a = j[has_next]
            vj = val[j_a]
            cur = val[idx_a]
            better = np.isfinite(vj) & (vj < cur)
            new_val[idx_a] = np.where(better, vj, cur)
            new_nxt[idx_a] = nxt[j_a]
            new_done[idx_a] = done[j_a]

            idx_b = active_idx[~has_next]
            if idx_b.size:
                new_done[idx_b] = True

        return new_val, new_nxt, new_done


def _flow_path_trace_pointer_doubling(
    downstream_flat,
    twi_flat,
    valid_flat,
    terminal_flat,
    rows,
    cols,
    progress_callback=None,
):
    n_cells = int(twi_flat.size)
    val, nxt, done = _flow_path_trace_init_kernel(
        downstream_flat, twi_flat, valid_flat, terminal_flat
    )

    active = valid_flat & ~done
    # Every round doubles the resolved path length, so convergence takes
    # ceil(log2(longest possible path)) rounds; +2 as a small safety
    # margin, and cycles are force-terminated once rounds are exhausted
    # (matching the previous implementation's best-effort cycle handling).
    max_rounds = max(1, int(np.ceil(np.log2(max(n_cells, 2)))) + 2)

    for round_idx in range(max_rounds):
        if not np.any(active):
            break
        val, nxt, done = _flow_path_trace_round_kernel(val, nxt, done)
        active = valid_flat & ~done

        if progress_callback is not None:
            frac = (round_idx + 1) / float(max_rounds)
            stage_progress = min(54, 51 + int(round(frac * 3.0)))
            progress_callback(
                stage_progress,
                f"5.3 Tracing flow paths to channel network... round {round_idx + 1}/{max_rounds}",
            )

    if progress_callback is not None:
        progress_callback(54, "5.3 Tracing flow paths to channel network... 100%")

    return np.where(valid_flat, val, np.nan).reshape((rows, cols))




class ScimapStandardAlgorithm(QgsProcessingAlgorithm):
    INPUT_DEM = 'INPUT_DEM'
    INPUT_LC = 'INPUT_LC'
    INPUT_RAIN = 'INPUT_RAIN'
    STREAM_THRESHOLD = 'STREAM_THRESHOLD'
    USE_STREAM_POWER = 'USE_STREAM_POWER'
    WBT_EXECUTABLE = 'WBT_EXECUTABLE'
    
    OUT_CONNECTIVITY = 'OUT_CONNECTIVITY'
    OUT_EROSION = 'OUT_EROSION'
    OUT_SCIMAP = 'OUT_SCIMAP'
    OUT_VECTOR_STREAM = 'OUT_VECTOR_STREAM'

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading # Use threads responsibly when integrating Gdal

    def createInstance(self):
        return ScimapStandardAlgorithm()

    def name(self):
        return 'scimapstandard'

    def displayName(self):
        return tr('SCIMAP Sediment')

    def group(self):
        return tr('Risk Mapping')

    def groupId(self):
        return 'scimap_risk_mapping'

    def icon(self):
        import os
        from qgis.PyQt.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(__file__), 'icon_sediment.svg')
        return QIcon(icon_path)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))
            
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_LC, tr('Land Cover Map / Risk Weighting')))
            
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RAIN, tr('Rainfall Map')))

        self.addParameter(QgsProcessingParameterNumber(
            self.STREAM_THRESHOLD, 
            tr('Stream Initiation Threshold (m²)'), 
            type=QgsProcessingParameterNumber.Double, 
            defaultValue=800000.0, 
            minValue=0.0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_STREAM_POWER,
            tr('Use stream power in erosion calculation'),
            defaultValue=True,
            optional=False,
        ))

        self.addParameter(QgsProcessingParameterFile(
            self.WBT_EXECUTABLE,
            tr('WhiteboxTools executable; leave blank to auto-detect'),
            behavior=QgsProcessingParameterFile.File,
            extension='*',
            fileFilter='All files (*)',
            optional=True
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_CONNECTIVITY, tr('Network Connectivity Risk')))
            
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_EROSION, tr('Erosion Risk')))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_SCIMAP, tr('SCIMAP Output Risk')))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_VECTOR_STREAM, tr('Vector Stream Network')))

    def compute_erosion_risk(self, accum_array, slope_deg, cell_area, use_stream_power=True):
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

    def compute_network_connectivity(
        self,
        d8_array,
        accum_array,
        slope_array,
        rainfall_scaled_array,
        mask_array=None,
        channel_mask=None,
        dem_array=None,
        progress_callback=None,
    ):
        # Compute TWI then apply SAGA-style flow-path trace connectivity.
        if progress_callback is not None:
            progress_callback(46, "5.1 Preparing TWI inputs for connectivity...")

        slope_arr = np.asarray(slope_array, dtype=np.float64)
        accum_arr = np.asarray(accum_array, dtype=np.float64)
        rain_arr = np.asarray(rainfall_scaled_array, dtype=np.float64)

        # Keep TWI numerically stable: clamp slope, avoid negative/zero log terms,
        # and only evaluate where all inputs are finite.
        slope_clamped = np.clip(slope_arr, 0.0, 89.0)
        slope_rad = slope_clamped * np.pi / 180.0

        wet_input = np.abs(accum_arr) * np.maximum(rain_arr, 1e-6) + 1.0
        tan_term = np.tan(slope_rad + 0.001) + 0.001

        twi_array = np.full_like(slope_arr, np.nan, dtype=np.float64)
        finite_inputs = np.isfinite(slope_arr) & np.isfinite(accum_arr) & np.isfinite(rain_arr)
        valid_twi = finite_inputs & (wet_input > 0.0) & (tan_term > 0.0)
        if np.any(valid_twi):
            twi_array[valid_twi] = np.log(wet_input[valid_twi]) - np.log(tan_term[valid_twi])

        if progress_callback is not None:
            progress_callback(48, "5.2 TWI prepared; running flow-path trace...")

        return self.compute_connectivity_flow_path_trace(
            d8_array,
            twi_array,
            mask_array,
            channel_mask,
            dem_array,
            progress_callback,
        )

    def compute_connectivity_flow_path_trace(
        self,
        d8_array,
        twi_array,
        mask_array=None,
        channel_mask=None,
        dem_array=None,
        progress_callback=None,
    ):
        """SAGA-style connectivity: trace each cell's D8 flow path downstream."""
        if shared_compute_connectivity_flow_path_trace is not None:
            if progress_callback is not None:
                progress_callback(51, "5.3 Running shared connectivity backend... 0%")

            conn_arr = shared_compute_connectivity_flow_path_trace(
                d8_array,
                twi_array,
                mask_array,
                channel_mask,
                dem_array,
            )
            if progress_callback is not None:
                progress_callback(54, "5.3 Running shared connectivity backend... 100%")
                progress_callback(55, "5.4 Shared connectivity backend finished; normalising output...")
                progress_callback(57, "5.5 Connectivity computation complete.")

            finite_any = np.isfinite(conn_arr).any()
            if finite_any and mask_array is not None:
                valid_cells = int(np.count_nonzero(mask_array))
                finite_valid = int(np.count_nonzero(np.isfinite(conn_arr) & (mask_array == 1)))
                finite_ratio = (finite_valid / float(valid_cells)) if valid_cells else 0.0
                if finite_ratio < 0.25:
                    if progress_callback is not None:
                        progress_callback(
                            55,
                            (
                                "5.4 Shared connectivity backend produced sparse finite coverage "
                                f"({finite_valid:,}/{valid_cells:,}, {finite_ratio:.3%}); retrying local backend..."
                            ),
                        )
                    logger.warning(
                        "Shared connectivity backend sparse finite coverage "
                        "(%s/%s, %.3f%%); falling back to local backend",
                        finite_valid,
                        valid_cells,
                        finite_ratio * 100.0,
                    )
                else:
                    return conn_arr
            elif finite_any:
                return conn_arr

            if progress_callback is not None:
                progress_callback(
                    55,
                    "5.4 Shared connectivity backend result was unusable; retrying local backend...",
                )
            logger.warning(
                "Shared connectivity backend result unusable; falling back to local backend"
            )

        return _compute_connectivity_flow_path_trace_local(
            d8_array,
            twi_array,
            mask_array,
            channel_mask,
            dem_array,
            progress_callback,
        )

    def describe_connectivity_backend(self):
        if shared_compute_connectivity_flow_path_trace is not None:
            return 'SAGA-style flow-path trace via shared app implementation'
        if njit is not None:
            return 'SAGA-style flow-path trace with local Numba parallel kernel'
        return 'SAGA-style flow-path trace with pure-Python fallback'

    def normalise_percentile(self, array, p_low=5, p_high=95):
        valid = np.isfinite(array)
        if not valid.any():
            return np.full_like(array, np.nan)
            
        lo, hi = np.nanpercentile(array[valid], [p_low, p_high])
        if hi <= lo:
            hi = lo + 1.0
        result = np.clip((array - lo) / (hi - lo), 0.0, 1.0)
        result[~valid] = np.nan
        return result

    def save_raster(self, np_array, output_path, reference_ds, data_type, mask_arr=None, wbt_compatible=False):
        """Helper to write Numpy arrays out via GDAL aligned to the input."""
        if mask_arr is not None:
            np_array = np.where(mask_arr, np_array, -9999)
        else:
            np_array = np.nan_to_num(np_array, nan=-9999)

        driver = gdal.GetDriverByName("GTiff")
        if wbt_compatible:
            # Some WhiteboxTools builds cannot read floating-point predictor
            # compressed GeoTIFFs (PREDICTOR=3), so write plain rasters.
            create_opts = [
                "TILED=NO",
                "COMPRESS=NONE",
                "BIGTIFF=IF_SAFER",
            ]
        else:
            predictor = "3" if data_type in (gdal.GDT_Float32, gdal.GDT_Float64) else "2"
            create_opts = [
                "TILED=YES",
                "COMPRESS=LZW",
                f"PREDICTOR={predictor}",
                "BIGTIFF=IF_SAFER",
            ]
        out_ds = driver.Create(
            output_path,
            np_array.shape[1],
            np_array.shape[0],
            1,
            data_type,
            options=create_opts,
        )
        out_ds.SetGeoTransform(reference_ds.GetGeoTransform())
        out_ds.SetProjection(reference_ds.GetProjection())
        out_ds.GetRasterBand(1).WriteArray(np_array)
        out_ds.GetRasterBand(1).SetNoDataValue(-9999)
        out_ds.FlushCache()
        out_ds = None

    def run_wbt(self, tool_name, args_dict, feedback):
        """Bypass the wbt_for_qgis plugin to avoid PyQt6 CrashExit errors in QGIS 4"""
        wbt_exe = None
        
        # We search locations in priority order, preferring a user-supplied path.
        candidates = [
            getattr(self, '_wbt_executable', ''),
            QgsSettings().value('SCIMAP/wbtExecutable', ''),
            QgsSettings().value("Wbt/executable", ""),
            os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins", "wbt_for_qgis", "WBT", "whitebox_tools"),
            "/Users/simreaney/miniconda/lib/python3.13/site-packages/whitebox/whitebox_tools",
            "/Users/simreaney/miniconda/bin/whitebox_tools" # Just in case
        ]
        
        for cp in candidates:
            if cp and os.path.exists(cp) and os.path.isfile(cp):
                wbt_exe = cp
                break
                
        if not wbt_exe:
            wbt_exe = shutil.which("whitebox_tools")
            
        if not wbt_exe:
            raise RuntimeError("Could not find the 'whitebox_tools' executable. Please make sure WhiteboxTools is installed on your system.")
            
        cmd = [wbt_exe, f'--run={tool_name}']
        for k, v in args_dict.items():
            if isinstance(v, bool):
                if v:
                    cmd.append(f'--{k}')
            else:
                cmd.append(f'--{k}={v}')
            
        feedback.pushInfo(f"Running WBT natively: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"WhiteboxTools failed: {res.stderr or res.stdout}")

    def create_pour_point_raster_safe(self, x, y, template_path, output_path, feedback=None):
        """Create pour-point raster using shared helper when available, else local fallback."""
        if callable(create_pour_point_raster):
            try:
                create_pour_point_raster(x, y, template_path, output_path)
                return
            except Exception as exc:
                if feedback is not None:
                    feedback.pushInfo(
                        f"Shared pour-point helper failed; using local fallback ({exc})."
                    )
                logger.warning("Shared create_pour_point_raster failed; falling back", exc_info=True)

        _create_pour_point_raster_local(x, y, template_path, output_path)

    def create_points_raster_safe(self, points, template_path, output_path, feedback=None):
        """Create a multi-point source raster (nearest-of-N target semantics)."""
        _create_points_raster_local(points, template_path, output_path)

    def run_cost_distance_safe(self, source_path, cost_path, output_path, feedback):
        """Run cost-distance with compatibility across shared/local helper signatures."""
        if callable(wbt_cost_distance):
            try:
                wbt_cost_distance(source_path, cost_path, output_path)
                if os.path.exists(output_path):
                    return
            except TypeError:
                # Shared helper variant that expects backlink output.
                backlink_path = os.path.join(
                    os.path.dirname(output_path),
                    f"{os.path.splitext(os.path.basename(output_path))[0]}_backlink.tif",
                )
                wbt_cost_distance(source_path, cost_path, output_path, backlink_path)
                if os.path.exists(output_path):
                    return
            except Exception as exc:
                feedback.pushInfo(
                    f"Shared cost-distance helper failed; using native WBT fallback ({exc})."
                )
                logger.warning("Shared wbt_cost_distance failed; falling back", exc_info=True)

        _run_wbt_cost_distance_local(
            lambda tool_name, args: self.run_wbt(tool_name, args, feedback),
            source_path,
            cost_path,
            output_path,
        )

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        lc_layer = self.parameterAsRasterLayer(parameters, self.INPUT_LC, context)
        rain_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RAIN, context)
        stream_m2_threshold = self.parameterAsDouble(parameters, self.STREAM_THRESHOLD, context)
        use_stream_power = self.parameterAsBool(parameters, self.USE_STREAM_POWER, context)
        wbt_executable = self.parameterAsFile(parameters, self.WBT_EXECUTABLE, context)

        # Persist a user-specified executable so future runs can reuse it.
        self._wbt_executable = ''
        if wbt_executable:
            wbt_executable = os.path.expanduser(wbt_executable)
            if os.path.isfile(wbt_executable):
                self._wbt_executable = wbt_executable
                QgsSettings().setValue('SCIMAP/wbtExecutable', wbt_executable)
            else:
                feedback.pushInfo(f"Provided WhiteboxTools path does not exist or is not a file: {wbt_executable}")

        out_conn_path = self.parameterAsOutputLayer(parameters, self.OUT_CONNECTIVITY, context)
        out_eros_path = self.parameterAsOutputLayer(parameters, self.OUT_EROSION, context)
        out_scimap_path = self.parameterAsOutputLayer(parameters, self.OUT_SCIMAP, context)
        out_vector_path = self.parameterAsOutputLayer(parameters, self.OUT_VECTOR_STREAM, context)

        # Prevent accidental output aliasing in the UI where SCIMAP and
        # connectivity are pointed at the same file.
        raster_output_paths = [
            path for path in [out_conn_path, out_eros_path, out_scimap_path]
            if path
        ]
        if len(raster_output_paths) != len(set(raster_output_paths)):
            raise RuntimeError(
                "Raster outputs must use different file paths."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback.setProgress(1)
            feedback.pushInfo("1. Filling DEM depressions...")
            dem_fill_path = os.path.join(tmpdir, "dem_fill.tif")
            
            # Using QGIS native WhiteboxTools integration workaround for Qt6
            self.run_wbt("BreachDepressions", {
                'dem': dem_layer.source(),
                'output': dem_fill_path
            }, feedback)

            feedback.setProgress(10)
            feedback.pushInfo(
                "2. Calculating Slope, FD8 Flow Accumulation, and D8 Pointer..."
            )
            slope_path = os.path.join(tmpdir, "slope.tif")
            self.run_wbt("Slope", {
                'dem': dem_fill_path, 'output': slope_path
            }, feedback)

            accum_path = os.path.join(tmpdir, "accum.tif")
            try:
                self.run_wbt("FD8FlowAccumulation", {
                    'dem': dem_fill_path,
                    'out_type': 'cells',
                    'exponent': 2.0,
                    'output': accum_path,
                }, feedback)
            except Exception:
                # Fallback for Whitebox builds that expose "i" instead of "dem".
                self.run_wbt("FD8FlowAccumulation", {
                    'i': dem_fill_path,
                    'out_type': 'cells',
                    'exponent': 2.0,
                    'output': accum_path,
                }, feedback)

            # D8 pointer is still required by connectivity routing and RasterStreamsToVector.
            d8_path = os.path.join(tmpdir, "d8.tif")
            feedback.pushInfo("D8 pointer encoding: Whitebox style (ESRI pointer flag omitted)")
            self.run_wbt("D8Pointer", {
                'dem': dem_fill_path,
                'output': d8_path,
                'esri_pntr': False,
            }, feedback)

            # Match the web app stream-network extraction threshold and use the
            # resulting channel cells as terminal connectivity routing targets.
            stream_vector_threshold = 250
            stream_tif_path = os.path.join(tmpdir, "stream.tif")
            self.run_wbt("ExtractStreams", {
                'flow_accum': accum_path, 'output': stream_tif_path, 'threshold': stream_vector_threshold
            }, feedback)

            # --- Reading Arrays Using GDAL ---
            feedback.setProgress(22)
            feedback.pushInfo("3. Loading arrays to process SCIMAP logic...")
            slope_ds = gdal.Open(slope_path)
            slope_arr = slope_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
            
            accum_ds = gdal.Open(accum_path)
            accum_arr = accum_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

            stream_ds = gdal.Open(stream_tif_path)
            stream_arr = stream_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
            
            d8_ds = gdal.Open(d8_path)
            d8_arr = d8_ds.GetRasterBand(1).ReadAsArray().astype(np.int32, copy=False)

            dem_fill_ds = gdal.Open(dem_fill_path)
            dem_fill_arr = dem_fill_ds.GetRasterBand(1).ReadAsArray().astype(np.float64, copy=False)

            dem_ds = gdal.Open(dem_layer.source())
            dem_arr = dem_ds.GetRasterBand(1).ReadAsArray()
            dem_nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
            # Create a boolean mask where True means it is valid data
            if dem_nodata is not None:
                mask_arr = (dem_arr != dem_nodata) & np.isfinite(dem_arr)
            else:
                mask_arr = np.isfinite(dem_arr)

            lc_ds = gdal.Open(lc_layer.source())
            lc_arr = lc_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

            rain_ds = gdal.Open(rain_layer.source())
            rain_arr = rain_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

            # Normalise rainfall for SCIMAP sediment
            rain_mean = np.nanmean(rain_arr[mask_arr]) if np.any(mask_arr) else np.nanmean(rain_arr)
            if rain_mean == 0 or np.isnan(rain_mean):
                rain_mean = 1.0
            rain_scaled = rain_arr / rain_mean
            
            # Get cell area from GDAL dataset
            gt = slope_ds.GetGeoTransform()
            cell_area = abs(gt[1] * gt[5])
            stream_cell_threshold = max(1.0, stream_m2_threshold / cell_area)

            feedback.setProgress(34)
            erosion_mode = "stream power" if use_stream_power else "upslope area only"
            feedback.pushInfo(f"4. Computing Erosion Risk ({erosion_mode})...")
            slope_arr = np.where(mask_arr, slope_arr, np.nan)
            accum_arr_masked = np.where(mask_arr, accum_arr, np.nan)
            rain_arr = np.where(mask_arr, rain_arr, np.nan)
            lc_arr = np.where(mask_arr, lc_arr, np.nan)
            channel_mask = (stream_arr > 0) & mask_arr

            eros_raw = self.compute_erosion_risk(
                accum_arr_masked,
                slope_arr,
                cell_area,
                use_stream_power=use_stream_power,
            )
            eros_raw = eros_raw * lc_arr
            erosion_risk = self.normalise_percentile(eros_raw, 5, 95)
            erosion_risk[~mask_arr] = np.nan

            feedback.setProgress(45)
            feedback.pushInfo("5. Computing Network Connectivity...")
            backend_description = self.describe_connectivity_backend()
            feedback.pushInfo(f"Connectivity backend: {backend_description}")
            if 'pure-Python fallback' in backend_description:
                feedback.pushInfo(
                    "Connectivity is running without Numba acceleration in QGIS Python; this can be very slow on large rasters."
                )

            def connectivity_progress(progress_value, message=None):
                feedback.setProgress(progress_value)
                if message:
                    feedback.pushInfo(message)

            connectivity = self.compute_network_connectivity(
                d8_arr,
                accum_arr,
                slope_arr,
                rain_scaled,
                mask_arr,
                channel_mask,
                dem_fill_arr,
                progress_callback=connectivity_progress,
            )

            feedback.setProgress(58)
            feedback.pushInfo("6. Computing FD8-based rainfall-weighted and SCIMAP routed proxies...")
            tiny = 1e-10
            source_risk = erosion_risk * connectivity

            catchment_area = np.abs(accum_arr_masked) * cell_area
            rainfall_weighted_catchment_area = catchment_area * rain_scaled
            scimap_routed_accum = source_risk * catchment_area

            scimap_output = np.where(
                np.abs(accum_arr_masked) >= stream_cell_threshold,
                scimap_routed_accum / (rainfall_weighted_catchment_area + tiny),
                np.nan,
            )
            scimap_output[~mask_arr] = np.nan

            # In some datasets the routed ratio can collapse numerically toward
            # connectivity. If that happens, preserve erosion weighting explicitly.
            stream_mask = (np.abs(accum_arr_masked) >= stream_cell_threshold) & mask_arr
            if np.any(stream_mask):
                scimap_vals = scimap_output[stream_mask]
                conn_vals = connectivity[stream_mask]
                finite = np.isfinite(scimap_vals) & np.isfinite(conn_vals)
                if np.any(finite):
                    max_abs_diff = float(np.nanmax(np.abs(scimap_vals[finite] - conn_vals[finite])))
                    if max_abs_diff < 1e-6:
                        feedback.pushInfo(
                            "SCIMAP routed output matched connectivity; using explicit erosion x connectivity product on stream cells."
                        )
                        scimap_output = np.where(stream_mask, source_risk, np.nan)
                        scimap_output[~mask_arr] = np.nan

            feedback.setProgress(70)
            feedback.pushInfo("7. Writing output rasters...")
            self.save_raster(erosion_risk, out_eros_path, slope_ds, gdal.GDT_Float32, mask_arr)
            self.save_raster(connectivity, out_conn_path, slope_ds, gdal.GDT_Float32, mask_arr)
            self.save_raster(scimap_output, out_scimap_path, slope_ds, gdal.GDT_Float32, mask_arr)

            feedback.setProgress(82)
            feedback.pushInfo(f"8. Generating Vector Stream Network (Threshold: {stream_vector_threshold} cells)...")

            temp_vector_path = os.path.join(tmpdir, "stream_vector.shp")
            self.run_wbt("RasterStreamsToVector", {
                'streams': stream_tif_path, 'd8_pntr': d8_path, 'output': temp_vector_path
            }, feedback)

            feedback.setProgress(90)
            if os.path.exists(temp_vector_path):
                try:
                    ds = ogr.Open(temp_vector_path, 1)
                    layer = ds.GetLayer()

                    field_defn = ogr.FieldDefn("Risk", ogr.OFTReal)
                    layer.CreateField(field_defn)

                    gt = slope_ds.GetGeoTransform()
                    inv_gt = gdal.InvGeoTransform(gt)

                    for feat in layer:
                        geom = feat.GetGeometryRef()
                        vals = []

                        sub_geoms = [geom] if geom.GetGeometryName() == "LINESTRING" else [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]

                        for sub_geom in sub_geoms:
                            pts = sub_geom.GetPoints()
                            if pts:
                                for pt in pts:
                                    x, y = pt[0], pt[1]
                                    col, row = gdal.ApplyGeoTransform(inv_gt, x, y)
                                    col, row = int(col), int(row)
                                    if 0 <= row < scimap_output.shape[0] and 0 <= col < scimap_output.shape[1]:
                                        v = scimap_output[row, col]
                                        if np.isfinite(v):
                                            vals.append(v)

                        if vals:
                            feat.SetField("Risk", float(np.mean(vals)))
                        layer.SetFeature(feat)

                    ds = None
                except Exception as e:
                    feedback.pushInfo(f"Could not calculate Risk values for streams: {e}")

            if os.path.exists(temp_vector_path):
                proj_wkt = slope_ds.GetProjection()

                feedback.setProgress(95)
                src_ds = ogr.Open(temp_vector_path, 0)
                if src_ds is None:
                    raise RuntimeError(f"Could not open stream vector for export: {temp_vector_path}")

                src_layer = src_ds.GetLayer(0)
                if src_layer is None:
                    raise RuntimeError(f"Stream vector contains no layers: {temp_vector_path}")

                is_gpkg = out_vector_path.endswith(".gpkg")
                driver_name = "GPKG" if is_gpkg else "ESRI Shapefile"
                output_layer_name = "streams" if is_gpkg else os.path.splitext(os.path.basename(out_vector_path))[0]
                driver = ogr.GetDriverByName(driver_name)
                if driver is None:
                    raise RuntimeError(f"Could not load OGR driver: {driver_name}")

                if os.path.exists(out_vector_path):
                    driver.DeleteDataSource(out_vector_path)

                dst_ds = driver.CreateDataSource(out_vector_path)
                if dst_ds is None:
                    raise RuntimeError(f"Could not create output vector: {out_vector_path}")

                srs = None
                if proj_wkt:
                    srs = osr.SpatialReference()
                    srs.ImportFromWkt(proj_wkt)

                dst_layer = dst_ds.CreateLayer(output_layer_name, srs=srs, geom_type=src_layer.GetGeomType())
                if dst_layer is None:
                    raise RuntimeError(f"Could not create output layer in vector file: {out_vector_path}")

                src_defn = src_layer.GetLayerDefn()
                for idx in range(src_defn.GetFieldCount()):
                    src_field_defn = src_defn.GetFieldDefn(idx)
                    src_field_name = src_field_defn.GetNameRef()
                    if is_gpkg and src_field_name and src_field_name.lower() in {"fid", "ogc_fid"}:
                        continue
                    dst_layer.CreateField(src_field_defn)

                dst_defn = dst_layer.GetLayerDefn()
                for src_feat in src_layer:
                    dst_feat = ogr.Feature(dst_defn)
                    dst_feat.SetFID(-1)

                    for idx in range(dst_defn.GetFieldCount()):
                        field_name = dst_defn.GetFieldDefn(idx).GetNameRef()
                        src_idx = src_defn.GetFieldIndex(field_name)
                        if src_idx >= 0:
                            dst_feat.SetField(idx, src_feat.GetField(src_idx))

                    geom = src_feat.GetGeometryRef()
                    if geom is not None:
                        dst_feat.SetGeometry(geom.Clone())
                    dst_layer.CreateFeature(dst_feat)
                    dst_feat = None

                dst_ds = None
                src_ds = None

            feedback.setProgress(100)

        return {
            self.OUT_CONNECTIVITY: out_conn_path,
            self.OUT_EROSION: out_eros_path,
            self.OUT_SCIMAP: out_scimap_path,
            self.OUT_VECTOR_STREAM: out_vector_path
        }


class ScimapNetworkIndexAlgorithm(ScimapStandardAlgorithm):
    OUT_NETWORK_INDEX = 'OUT_NETWORK_INDEX'

    def createInstance(self):
        return ScimapNetworkIndexAlgorithm()

    def name(self):
        return 'scimapnetworkindex'

    def displayName(self):
        return tr('Network Index')

    def group(self):
        return tr('Risk Mapping')

    def groupId(self):
        return 'scimap_network_index'

    def icon(self):
        import os
        from qgis.PyQt.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(__file__), 'icon_network.svg')
        return QIcon(icon_path)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterNumber(
            self.STREAM_THRESHOLD,
            tr('Stream Initiation Threshold (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0,
            minValue=0.0))

        self.addParameter(QgsProcessingParameterFile(
            self.WBT_EXECUTABLE,
            tr('WhiteboxTools executable; leave blank to auto-detect'),
            behavior=QgsProcessingParameterFile.File,
            extension='*',
            fileFilter='All files (*)',
            optional=True
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_NETWORK_INDEX, tr('Network Index')))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        stream_m2_threshold = self.parameterAsDouble(parameters, self.STREAM_THRESHOLD, context)
        wbt_executable = self.parameterAsFile(parameters, self.WBT_EXECUTABLE, context)

        self._wbt_executable = ''
        if wbt_executable:
            wbt_executable = os.path.expanduser(wbt_executable)
            if os.path.isfile(wbt_executable):
                self._wbt_executable = wbt_executable
                QgsSettings().setValue('SCIMAP/wbtExecutable', wbt_executable)
            else:
                feedback.pushInfo(f"Provided WhiteboxTools path does not exist or is not a file: {wbt_executable}")

        out_network_index_path = self.parameterAsOutputLayer(parameters, self.OUT_NETWORK_INDEX, context)

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback.setProgress(5)
            feedback.pushInfo("1. Filling DEM depressions...")
            dem_fill_path = os.path.join(tmpdir, "dem_fill.tif")
            self.run_wbt("BreachDepressions", {
                'dem': dem_layer.source(),
                'output': dem_fill_path
            }, feedback)

            feedback.setProgress(20)
            feedback.pushInfo("2. Calculating slope, FD8 accumulation, and D8 pointer...")
            slope_path = os.path.join(tmpdir, "slope.tif")
            self.run_wbt("Slope", {
                'dem': dem_fill_path,
                'output': slope_path,
            }, feedback)

            accum_path = os.path.join(tmpdir, "accum.tif")
            try:
                self.run_wbt("FD8FlowAccumulation", {
                    'dem': dem_fill_path,
                    'out_type': 'cells',
                    'exponent': 2.0,
                    'output': accum_path,
                }, feedback)
            except Exception:
                self.run_wbt("FD8FlowAccumulation", {
                    'i': dem_fill_path,
                    'out_type': 'cells',
                    'exponent': 2.0,
                    'output': accum_path,
                }, feedback)

            d8_path = os.path.join(tmpdir, "d8.tif")
            self.run_wbt("D8Pointer", {
                'dem': dem_fill_path,
                'output': d8_path,
                'esri_pntr': False,
            }, feedback)

            stream_tif_path = os.path.join(tmpdir, "stream.tif")

            slope_ds = gdal.Open(slope_path)
            gt = slope_ds.GetGeoTransform()
            cell_area = abs(gt[1] * gt[5])
            stream_cell_threshold = max(1.0, stream_m2_threshold / cell_area)
            self.run_wbt("ExtractStreams", {
                'flow_accum': accum_path,
                'output': stream_tif_path,
                'threshold': stream_cell_threshold,
            }, feedback)

            feedback.setProgress(45)
            feedback.pushInfo("3. Loading arrays and computing network index...")
            slope_arr = slope_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
            accum_ds = gdal.Open(accum_path)
            accum_arr = accum_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
            d8_ds = gdal.Open(d8_path)
            d8_arr = d8_ds.GetRasterBand(1).ReadAsArray().astype(np.int32, copy=False)
            dem_fill_ds = gdal.Open(dem_fill_path)
            dem_fill_arr = dem_fill_ds.GetRasterBand(1).ReadAsArray().astype(np.float64, copy=False)
            stream_ds = gdal.Open(stream_tif_path)
            stream_arr = stream_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

            dem_ds = gdal.Open(dem_layer.source())
            dem_arr = dem_ds.GetRasterBand(1).ReadAsArray()
            dem_nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
            if dem_nodata is not None:
                mask_arr = (dem_arr != dem_nodata) & np.isfinite(dem_arr)
            else:
                mask_arr = np.isfinite(dem_arr)

            slope_arr = np.where(mask_arr, slope_arr, np.nan)
            accum_arr = np.where(mask_arr, accum_arr, np.nan)
            channel_mask = (stream_arr > 0) & mask_arr

            network_index = self.compute_network_connectivity(
                d8_arr,
                accum_arr,
                slope_arr,
                np.ones_like(accum_arr, dtype=np.float32),
                mask_arr,
                channel_mask,
                dem_fill_arr,
            )

            feedback.setProgress(85)
            feedback.pushInfo("4. Writing network index raster...")
            self.save_raster(network_index, out_network_index_path, slope_ds, gdal.GDT_Float32, mask_arr)

            feedback.setProgress(100)

        return {
            self.OUT_NETWORK_INDEX: out_network_index_path,
        }

    def save_raster(self, np_array, output_path, reference_ds, data_type, mask_arr=None):
        """Helper to write Numpy arrays out via GDAL aligned to the input"""
        if mask_arr is not None:
            # Enforce nodata on arrays before saving
            np_array = np.where(mask_arr, np_array, -9999)
        else:
            np_array = np.nan_to_num(np_array, nan=-9999)

        driver = gdal.GetDriverByName("GTiff")
        # Use compressed, tiled GeoTIFFs to reduce temporary disk usage for
        # large outputs (notably routed accumulation rasters).
        predictor = "3" if data_type in (gdal.GDT_Float32, gdal.GDT_Float64) else "2"
        create_opts = [
            "TILED=YES",
            "COMPRESS=LZW",
            f"PREDICTOR={predictor}",
            "BIGTIFF=IF_SAFER",
        ]
        out_ds = driver.Create(
            output_path,
            np_array.shape[1],
            np_array.shape[0],
            1,
            data_type,
            options=create_opts,
        )
        out_ds.SetGeoTransform(reference_ds.GetGeoTransform())
        out_ds.SetProjection(reference_ds.GetProjection())
        out_ds.GetRasterBand(1).WriteArray(np_array)
        out_ds.GetRasterBand(1).SetNoDataValue(-9999)
        out_ds.FlushCache()
        out_ds = None


class ScimapFloodAlgorithm(ScimapStandardAlgorithm):
    """SCIMAP Flood - operates on pre-computed input rasters.

    Mirrors core.py's SCIMAPFlood exactly:
      - connectivity_map  → pre-computed connectivity raster
      - runoff_map        → pre-computed runoff / land cover weights raster
      - rainfall_maps     → multiple rainfall pattern rasters (all are used)
      - overland_flow_maps → pre-computed overland flow distance rasters

    Every input raster has its declared nodata value, and any negative
    value, treated as invalid (NaN).

    Algorithm:
        base = connectivity × runoff   (raw, unscaled)
        rainfall_norm = (rainfall - raster_min) / (raster_max - raster_min)
        overland_norm = peak-at-median stretch to [0, 1] using the
            raster's min, median and max (1.0 at the median, falling off
            linearly to 0.0 at the min/max)
        For each (rainfall, overland) combination:
            product = base × rainfall_norm × overland_norm
        mean  = per-pixel mean(product) over combinations valid at that pixel
        stdev = per-pixel population stdev(product) over the same set
        output mean is finally divided by its own global maximum

    Reference:
        Reaney, Sim M. (2022) Spatial targeting of nature-based solutions
        for flood risk management within river catchments.
        Journal of Flood Risk Management e12803.
    """

    INPUT_CONNECTIVITY = 'INPUT_CONNECTIVITY'
    INPUT_RUNOFF = 'INPUT_RUNOFF'
    INPUT_RAINFALL_MAPS = 'INPUT_RAINFALL_MAPS'
    INPUT_OFD_MAPS = 'INPUT_OFD_MAPS'

    OUT_MEAN = 'OUT_MEAN'
    OUT_STDEV = 'OUT_STDEV'

    def createInstance(self):
        return ScimapFloodAlgorithm()

    def name(self):
        return 'scimapflood'

    def displayName(self):
        return tr('SCIMAP Flood')

    def group(self):
        return tr('Risk Mapping')

    def groupId(self):
        return 'scimap_risk_mapping'

    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(__file__), 'icon_flood.svg')
        return QIcon(icon_path)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_CONNECTIVITY,
            tr('Connectivity Raster (pre-computed)')))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RUNOFF,
            tr('Runoff / Land Cover Weights Raster (pre-computed)')))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_RAINFALL_MAPS,
            tr('Rainfall Pattern Rasters'),
            layerType=QgsProcessing.TypeRaster,
        ))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_OFD_MAPS,
            tr('Overland Flow Distance Rasters (pre-computed)'),
            layerType=QgsProcessing.TypeRaster,
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_MEAN, tr('SCIMAP-Flood Mean')))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_STDEV, tr('SCIMAP-Flood Standard Deviation')))

    def processAlgorithm(self, parameters, context, feedback):
        # ── Citation (mirrors pySCIMAP-Flood_2026.py print statements) ─
        feedback.pushInfo('SCIMAP-Flood. Reaney 2022-2026')
        feedback.pushInfo(
            'Reaney, Sim M. (2022) Spatial targeting of nature-based solutions '
            'for flood risk management within river catchments. '
            'Journal of Flood Risk Management e12803'
        )
        feedback.pushInfo('')

        conn_layer = self.parameterAsRasterLayer(parameters, self.INPUT_CONNECTIVITY, context)
        runoff_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RUNOFF, context)
        rainfall_layers = self.parameterAsLayerList(parameters, self.INPUT_RAINFALL_MAPS, context) or []
        ofd_layers = self.parameterAsLayerList(parameters, self.INPUT_OFD_MAPS, context) or []

        out_mean_path = self.parameterAsOutputLayer(parameters, self.OUT_MEAN, context)
        out_stdev_path = self.parameterAsOutputLayer(parameters, self.OUT_STDEV, context)

        if out_mean_path == out_stdev_path:
            raise RuntimeError('Output rasters must use different file paths.')
        if not rainfall_layers:
            raise RuntimeError('At least one rainfall pattern raster is required.')
        if not ofd_layers:
            raise RuntimeError('At least one overland flow distance raster is required.')

        # This runs window-by-window (chunk_size x chunk_size tiles) rather
        # than loading every input raster fully into memory, mirroring
        # core.py's rasterio-based windowed SCIMAP-Flood implementation. Only
        # one tile's worth of connectivity/runoff/rainfall/OFD data is ever
        # resident at once, so peak RAM no longer scales with raster size.
        chunk_size = FLOOD_DEFAULT_CHUNK_SIZE

        # ── 1. Open connectivity raster ──────────────────────────────────
        feedback.setProgress(2)
        feedback.pushInfo('1. Opening connectivity raster...')
        conn_ds = gdal.Open(conn_layer.source())
        if conn_ds is None:
            raise RuntimeError(f'Could not open connectivity raster: {conn_layer.source()}')
        conn_band = conn_ds.GetRasterBand(1)
        conn_nodata = conn_band.GetNoDataValue()

        reference_gt = conn_ds.GetGeoTransform()
        reference_size = (conn_ds.RasterXSize, conn_ds.RasterYSize)
        width, height = reference_size

        open_datasets = [conn_ds]

        def _open_aligned_raster(layer, label):
            """Open a raster and verify it matches the reference grid, without reading it."""
            source = layer.source() if hasattr(layer, 'source') else str(layer)
            ds = gdal.Open(source)
            if ds is None:
                raise RuntimeError(f'Could not open {label}: {source}')
            if ds.RasterXSize != reference_size[0] or ds.RasterYSize != reference_size[1]:
                raise RuntimeError(f'{label} does not match the connectivity grid size.')
            gt = ds.GetGeoTransform()
            if any(abs(float(gt[i]) - float(reference_gt[i])) > 1e-9 for i in range(6)):
                raise RuntimeError(f'{label} does not match the connectivity grid alignment.')
            open_datasets.append(ds)
            return ds

        # ── 2. Open runoff / land cover weights raster ───────────────────
        # Used as-is, matching core.py (`base = connectivity * runoff`, no
        # percentile rescaling).
        feedback.setProgress(8)
        feedback.pushInfo('2. Opening runoff / land cover weights raster...')
        runoff_ds = _open_aligned_raster(runoff_layer, 'runoff raster')
        runoff_band = runoff_ds.GetRasterBand(1)
        runoff_nodata = runoff_band.GetNoDataValue()

        # ── 3. Scan rainfall patterns for their global min/max ───────────
        feedback.setProgress(15)
        feedback.pushInfo('3. Scanning rainfall pattern rasters for min/max...')
        rainfall_bands = []
        rainfall_nodata = []
        rf_min_max = []
        for idx, layer in enumerate(rainfall_layers):
            ds = _open_aligned_raster(layer, f'rainfall pattern raster {idx + 1}')
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            rf_min, rf_max = _flood_stream_min_max(band, nodata, width, height, chunk_size)
            if not np.isfinite(rf_min) or not np.isfinite(rf_max):
                raise RuntimeError(f'Rainfall pattern raster {idx + 1} has no valid data.')
            if rf_max <= rf_min:
                raise RuntimeError(
                    f'Rainfall pattern raster {idx + 1} has no valid data range (min == max).'
                )
            rainfall_bands.append(band)
            rainfall_nodata.append(nodata)
            rf_min_max.append((rf_min, rf_max))

        # ── 4. Scan overland flow distance rasters for their global stats ─
        feedback.setProgress(30)
        feedback.pushInfo(
            f'4. Scanning {len(ofd_layers)} overland flow distance raster(s) for min/median/max...'
        )
        ofd_bands = []
        ofd_nodata = []
        ofd_stats = []
        for idx, layer in enumerate(ofd_layers):
            feedback.pushInfo(
                f'Scanning overland flow distance raster {idx + 1}/{len(ofd_layers)}'
            )
            ds = _open_aligned_raster(layer, f'OFD raster {idx + 1}')
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            ofd_min, ofd_max = _flood_stream_min_max(band, nodata, width, height, chunk_size)
            if not np.isfinite(ofd_min) or not np.isfinite(ofd_max):
                raise RuntimeError(f'OFD raster {idx + 1} has no valid data.')
            if ofd_max <= ofd_min:
                raise RuntimeError(
                    f'OFD raster {idx + 1} has no valid data range (min == max).'
                )
            ofd_median = _flood_stream_median(band, nodata, width, height, chunk_size)
            if ofd_median == 0:
                raise RuntimeError(f'OFD raster {idx + 1} has an invalid (zero) median.')
            ofd_bands.append(band)
            ofd_nodata.append(nodata)
            ofd_stats.append((ofd_min, ofd_median, ofd_max))

        # ── 5. SCIMAP-Flood calculation: mean + stdev, windowed ───────────
        # Mirrors core.py exactly: base = connectivity * runoff; for every
        # (overland, rainfall) combination, product = base * rainfall_norm *
        # overland_norm; mean/stdev are accumulated per-pixel so only
        # combinations that are valid at a given pixel contribute there.
        # Unlike the previous implementation this now runs one window at a
        # time; raw (unnormalised) mean/stdev are staged to temporary GDAL
        # rasters, since the mean can only be normalised by its own global
        # maximum once every window has been visited.
        n_combos = len(ofd_bands) * len(rainfall_bands)
        feedback.pushInfo(
            f'5. Running windowed SCIMAP-Flood calculation over {n_combos} combination(s) '
            f'({len(rainfall_bands)} rainfall × {len(ofd_bands)} OFD, '
            f'{chunk_size}x{chunk_size} tiles)...'
        )

        tmp_dir = tempfile.mkdtemp(prefix='scimap_flood_')
        driver = gdal.GetDriverByName('GTiff')
        create_opts = ['TILED=YES', 'COMPRESS=LZW', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER']

        mean_tmp_path = os.path.join(tmp_dir, 'mean_tmp.tif')
        stdev_tmp_path = os.path.join(tmp_dir, 'stdev_tmp.tif')

        mean_tmp_ds = driver.Create(mean_tmp_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        mean_tmp_ds.SetGeoTransform(reference_gt)
        mean_tmp_ds.SetProjection(conn_ds.GetProjection())
        mean_tmp_band = mean_tmp_ds.GetRasterBand(1)
        mean_tmp_band.SetNoDataValue(float('nan'))

        stdev_tmp_ds = driver.Create(stdev_tmp_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        stdev_tmp_ds.SetGeoTransform(reference_gt)
        stdev_tmp_ds.SetProjection(conn_ds.GetProjection())
        stdev_tmp_band = stdev_tmp_ds.GetRasterBand(1)
        stdev_tmp_band.SetNoDataValue(float('nan'))

        def _write_mean_window(window, array):
            col_off, row_off, _, _ = window
            mean_tmp_band.WriteArray(array, col_off, row_off)

        def _write_stdev_window(window, array):
            col_off, row_off, _, _ = window
            stdev_tmp_band.WriteArray(array, col_off, row_off)

        def _main_pass_progress(done, total):
            feedback.setProgress(35 + int(45 * done / max(total, 1)))

        try:
            mean_max = _run_scimap_flood_windowed(
                conn_band, conn_nodata,
                runoff_band, runoff_nodata,
                rainfall_bands, rainfall_nodata, rf_min_max,
                ofd_bands, ofd_nodata, ofd_stats,
                width, height,
                _write_mean_window, _write_stdev_window,
                chunk_size=chunk_size,
                progress_callback=_main_pass_progress,
            )
        except RuntimeError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        mean_tmp_band.FlushCache()
        stdev_tmp_band.FlushCache()

        # ── 6. Normalise mean output by its global maximum, windowed ─────
        feedback.setProgress(85)
        feedback.pushInfo('6. Normalising mean output and writing final rasters...')

        mean_out_ds = driver.Create(out_mean_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        mean_out_ds.SetGeoTransform(reference_gt)
        mean_out_ds.SetProjection(conn_ds.GetProjection())
        mean_out_band = mean_out_ds.GetRasterBand(1)
        mean_out_band.SetNoDataValue(-9999)

        stdev_out_ds = driver.Create(out_stdev_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        stdev_out_ds.SetGeoTransform(reference_gt)
        stdev_out_ds.SetProjection(conn_ds.GetProjection())
        stdev_out_band = stdev_out_ds.GetRasterBand(1)
        stdev_out_band.SetNoDataValue(-9999)

        for window in _flood_iter_windows(width, height, chunk_size):
            col_off, row_off, win_width, win_height = window

            mean_window = mean_tmp_band.ReadAsArray(col_off, row_off, win_width, win_height)
            mean_norm_window = np.where(
                np.isfinite(mean_window), mean_window / mean_max, np.nan
            ).astype(np.float32, copy=False)
            mean_norm_window = np.nan_to_num(mean_norm_window, nan=-9999)
            mean_out_band.WriteArray(mean_norm_window, col_off, row_off)

            stdev_window = stdev_tmp_band.ReadAsArray(col_off, row_off, win_width, win_height)
            stdev_window = np.nan_to_num(stdev_window, nan=-9999)
            stdev_out_band.WriteArray(stdev_window, col_off, row_off)

        mean_out_band.FlushCache()
        stdev_out_band.FlushCache()
        mean_out_ds = None
        stdev_out_ds = None

        mean_tmp_ds = None
        stdev_tmp_ds = None
        shutil.rmtree(tmp_dir, ignore_errors=True)

        feedback.setProgress(100)
        feedback.pushInfo('SCIMAP-Flood complete.')
        feedback.pushInfo('Saved outputs:')
        feedback.pushInfo(f'- mean: {out_mean_path}')
        feedback.pushInfo(f'- stdev: {out_stdev_path}')

        return {
            self.OUT_MEAN: out_mean_path,
            self.OUT_STDEV: out_stdev_path,
        }


class ScimapOverlandFlowDistanceAlgorithm(ScimapStandardAlgorithm):
    """Overland Flow Distance to Point.

    Computes the downslope flow-path distance across the land surface from
    each cell to the nearest of one or more target points, using
    WhiteboxTools' DownslopeDistanceToStream tool exclusively (no GRASS or
    SAGA dependency).

    Unlike a cost-distance surface, this follows the D8 downslope flow
    direction derived from the DEM: water only travels downhill, so cells
    that do not drain to a target point receive no distance (NoData)
    instead of an omnidirectional straight-line-like distance.
    """

    INPUT_DEM = 'INPUT_DEM'
    INPUT_POINTS = 'INPUT_POINTS'
    INPUT_CHANNELS = 'INPUT_CHANNELS'
    SNAP_DISTANCE = 'SNAP_DISTANCE'
    FILL_DEPRESSIONS = 'FILL_DEPRESSIONS'
    WBT_EXECUTABLE = 'WBT_EXECUTABLE'

    OUT_DISTANCE = 'OUT_DISTANCE'

    def createInstance(self):
        return ScimapOverlandFlowDistanceAlgorithm()

    def name(self):
        return 'scimapoverlandflowdistance'

    def displayName(self):
        return tr('Overland Flow Distance to Point')

    def group(self):
        return tr('Risk Mapping')

    def groupId(self):
        return 'scimap_risk_mapping'

    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(__file__), 'icon_ofd.svg')
        return QIcon(icon_path)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_POINTS,
            tr('Target Point(s)'),
            types=[QgsProcessing.TypeVectorPoint],
        ))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_CHANNELS,
            tr('Channel Network (optional; snaps target point(s) to the nearest channel)'),
            types=[QgsProcessing.TypeVectorLine],
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_DISTANCE,
            tr('Maximum snap distance to channel (map units; ignored if no channel network supplied)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0,
            minValue=0.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL_DEPRESSIONS,
            tr('Breach DEM depressions before computing flow direction (recommended, avoids broken flow paths at sinks)'),
            defaultValue=True,
        ))

        self.addParameter(QgsProcessingParameterFile(
            self.WBT_EXECUTABLE,
            tr('WhiteboxTools executable; leave blank to auto-detect'),
            behavior=QgsProcessingParameterFile.File,
            extension='*',
            fileFilter='All files (*)',
            optional=True
        ))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_DISTANCE, tr('Overland Flow Travel Distance')))

    def _snap_points_to_channels(self, points, channels_layer, dem_crs, context, max_distance, feedback=None):
        """Snap each (x, y) target point onto the nearest line in channels_layer.

        Points further than max_distance from any channel (when max_distance > 0)
        are left unsnapped, since a distant "nearest" match is more likely a data
        error than an intended snap target.
        """
        to_channel_crs = None
        to_dem_crs = None
        if channels_layer.crs().isValid() and dem_crs.isValid() and channels_layer.crs() != dem_crs:
            to_channel_crs = QgsCoordinateTransform(dem_crs, channels_layer.crs(), context.transformContext())
            to_dem_crs = QgsCoordinateTransform(channels_layer.crs(), dem_crs, context.transformContext())

        index = QgsSpatialIndex(channels_layer.getFeatures())
        geometries = {f.id(): f.geometry() for f in channels_layer.getFeatures()}

        snapped_points = []
        snapped_count = 0
        for x, y in points:
            query_point = QgsPointXY(x, y)
            if to_channel_crs is not None:
                query_point = to_channel_crs.transform(query_point)

            best_point = None
            best_dist_sq = None
            for fid in index.nearestNeighbor(query_point, 5):
                geometry = geometries.get(fid)
                if geometry is None or geometry.isEmpty():
                    continue
                dist_sq, closest_point, _, _ = geometry.closestSegmentWithContext(query_point)
                if best_dist_sq is None or dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_point = closest_point

            if best_point is None:
                snapped_points.append((x, y))
                continue

            distance = math.sqrt(best_dist_sq)
            if max_distance and max_distance > 0 and distance > max_distance:
                snapped_points.append((x, y))
                continue

            if to_dem_crs is not None:
                best_point = to_dem_crs.transform(best_point)

            snapped_points.append((float(best_point.x()), float(best_point.y())))
            snapped_count += 1

        if feedback is not None:
            feedback.pushInfo(
                f'   Snapped {snapped_count} of {len(points)} target point(s) to the channel network.'
            )

        return snapped_points

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        points_layer = self.parameterAsVectorLayer(parameters, self.INPUT_POINTS, context)
        channels_layer = self.parameterAsVectorLayer(parameters, self.INPUT_CHANNELS, context)
        snap_distance = self.parameterAsDouble(parameters, self.SNAP_DISTANCE, context)
        fill_depressions = self.parameterAsBoolean(parameters, self.FILL_DEPRESSIONS, context)
        wbt_executable = self.parameterAsFile(parameters, self.WBT_EXECUTABLE, context)

        self._wbt_executable = ''
        if wbt_executable:
            wbt_executable = os.path.expanduser(wbt_executable)
            if os.path.isfile(wbt_executable):
                self._wbt_executable = wbt_executable
                QgsSettings().setValue('SCIMAP/wbtExecutable', wbt_executable)
            else:
                feedback.pushInfo(f"Provided WhiteboxTools path does not exist or is not a file: {wbt_executable}")

        out_distance_path = self.parameterAsOutputLayer(parameters, self.OUT_DISTANCE, context)

        dem_ds = gdal.Open(dem_layer.source())
        if dem_ds is None:
            raise RuntimeError(f'Could not open DEM: {dem_layer.source()}')
        dem_band = dem_ds.GetRasterBand(1)
        dem_arr = dem_band.ReadAsArray().astype(np.float32, copy=False)
        dem_nodata = dem_band.GetNoDataValue()
        if dem_nodata is not None:
            if np.isfinite(dem_nodata):
                dem_mask = (~np.isclose(dem_arr, dem_nodata)) & np.isfinite(dem_arr)
            else:
                dem_mask = (dem_arr != dem_nodata) & np.isfinite(dem_arr)
        else:
            dem_mask = np.isfinite(dem_arr)

        if not np.any(dem_mask):
            raise RuntimeError('DEM contains no valid cells.')

        reference_gt = dem_ds.GetGeoTransform()
        reference_size = (dem_ds.RasterXSize, dem_ds.RasterYSize)

        def _read_aligned_raster(layer_or_path, label):
            source = layer_or_path.source() if hasattr(layer_or_path, 'source') else str(layer_or_path)
            ds = gdal.Open(source)
            if ds is None:
                raise RuntimeError(f'Could not open {label}: {source}')
            if ds.RasterXSize != reference_size[0] or ds.RasterYSize != reference_size[1]:
                raise RuntimeError(f'{label} does not match the DEM grid size.')
            gt = ds.GetGeoTransform()
            if any(abs(float(gt[i]) - float(reference_gt[i])) > 1e-9 for i in range(6)):
                raise RuntimeError(f'{label} does not match the DEM grid alignment.')
            band = ds.GetRasterBand(1)
            arr = band.ReadAsArray().astype(np.float32, copy=False)
            nodata = band.GetNoDataValue()
            if nodata is not None:
                if np.isfinite(nodata):
                    arr[np.isclose(arr, nodata)] = np.nan
                else:
                    arr[arr == nodata] = np.nan
            arr[~np.isfinite(arr)] = np.nan
            return ds, arr

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback.setProgress(5)

            if fill_depressions:
                feedback.pushInfo('1. Breaching DEM depressions before computing flow direction...')
                dem_fill_path = os.path.join(tmpdir, 'dem_fill.tif')
                self.run_wbt('BreachDepressions', {
                    'dem': dem_layer.source(),
                    'output': dem_fill_path,
                }, feedback)
                flow_dem_path = dem_fill_path
            else:
                feedback.pushInfo('1. Using DEM as supplied for flow direction (depression filling disabled)...')
                flow_dem_path = dem_layer.source()

            feedback.setProgress(30)
            feedback.pushInfo('2. Rasterising target point(s)...')

            target_points = []
            transform = None
            if points_layer.crs().isValid() and dem_layer.crs().isValid() and points_layer.crs() != dem_layer.crs():
                transform = QgsCoordinateTransform(points_layer.crs(), dem_layer.crs(), context.transformContext())

            for feature in points_layer.getFeatures():
                geometry = feature.geometry()
                if geometry is None or geometry.isEmpty():
                    continue
                if transform is not None:
                    geometry = geometry.clone()
                    geometry.transform(transform)
                points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
                for point in points:
                    target_points.append((float(point.x()), float(point.y())))

            if not target_points:
                raise RuntimeError('Target point layer does not contain any valid points.')

            if channels_layer is not None:
                feedback.pushInfo(
                    f'   Snapping {len(target_points)} target point(s) to the nearest channel...'
                )
                target_points = self._snap_points_to_channels(
                    target_points, channels_layer, dem_layer.crs(), context, snap_distance, feedback
                )

            pt_path = os.path.join(tmpdir, 'points.tif')
            self.create_points_raster_safe(target_points, dem_layer.source(), pt_path, feedback)

            feedback.setProgress(50)
            feedback.pushInfo(
                f'3. Computing WhiteboxTools downslope flow-path distance to nearest of '
                f'{len(target_points)} target point(s)...'
            )
            dist_path = os.path.join(tmpdir, 'distance.tif')
            self.run_wbt('DownslopeDistanceToStream', {
                'dem': flow_dem_path,
                'streams': pt_path,
                'output': dist_path,
            }, feedback)

            _, dist_arr = _read_aligned_raster(dist_path, 'overland flow distance raster')
            # Cells whose downslope flow path never reaches a target point (e.g. they
            # drain off the grid or into a different catchment) get no distance, rather
            # than being coerced to 0 and looking like they're at the target.
            valid_output_mask = dem_mask & np.isfinite(dist_arr)
            dist_arr = np.where(valid_output_mask, np.maximum(dist_arr, 0.0), 0.0)

            feedback.setProgress(90)
            feedback.pushInfo('4. Writing output raster...')
            self.save_raster(dist_arr, out_distance_path, dem_ds, gdal.GDT_Float32, valid_output_mask)

            feedback.setProgress(100)
            feedback.pushInfo('Overland flow distance complete.')
            feedback.pushInfo(f'- distance: {out_distance_path}')

        return {
            self.OUT_DISTANCE: out_distance_path,
        }