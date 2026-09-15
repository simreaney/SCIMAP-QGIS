"""Network index of connectivity (SAGA-style flow-path trace).

Ported from ``processing/wb_utils.py`` in the SCIMAP web application:
``compute_twi``, ``build_downstream_index``, ``build_downstream_index_from_dem``
and ``compute_connectivity_flow_path_trace``.

Each cell's connectivity score is the minimum topographic wetness index found
along its downstream flow path up to the channel network, normalised to [0, 1]
between the 5th and 95th percentiles.
"""

import numpy as np

from ._numba import njit, prange
from .erosion import normalise_percentile

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


# Percentage Downslope Saturated Length (PDSL): ported from SAGA-GIS's
# CHCIC::downslopeSaturatedLength. Unlike the flow-path trace above, each
# cell's score depends on its own threshold (its wetness value), so the
# result can't be folded with pointer doubling — every cell independently
# walks its downstream D8 path (terminating at a channel cell or the grid
# edge) counting how many path cells are at least as wet as itself.
if njit is not None:
    @njit(cache=True, parallel=True)
    def _pdsl_kernel(downstream_flat, wet_flat, valid_flat, rows, cols):
        rows_i = np.int64(rows)
        cols_i = np.int64(cols)
        n_cells = np.int64(wet_flat.size)
        pdsl = np.empty(n_cells, dtype=np.float64)
        pdsl[:] = np.nan

        for fi in prange(n_cells):
            fi_i = np.int64(fi)
            if not valid_flat[fi_i]:
                continue

            min_wet = wet_flat[fi_i]
            idx = fi_i
            idx_fast = fi_i
            steps = np.int64(0)
            saturated = np.int64(0)

            while True:
                idx_next = downstream_flat[idx]
                if idx_next < 0:
                    break

                v = wet_flat[idx_next]
                if v == v and v >= min_wet:
                    saturated += 1

                idx = idx_next
                steps += 1

                # Cycle detection for cyclic D8 pointers.
                idx_fast = downstream_flat[idx_fast]
                if idx_fast < 0:
                    continue
                idx_fast = downstream_flat[idx_fast]
                if idx_fast >= 0 and idx == idx_fast:
                    break

                if steps >= n_cells:
                    break

            pdsl[fi_i] = 1.0 if steps == 0 else saturated / steps

        return pdsl.reshape((rows_i, cols_i))

else:
    def _pdsl_kernel(downstream_flat, wet_flat, valid_flat, rows, cols):
        n_cells = wet_flat.size
        pdsl = np.full(n_cells, np.nan, dtype=np.float64)

        for fi in range(n_cells):
            if not valid_flat[fi]:
                continue

            min_wet = wet_flat[fi]
            idx = fi
            idx_fast = fi
            steps = 0
            saturated = 0

            while True:
                idx_next = downstream_flat[idx]
                if idx_next < 0:
                    break

                v = wet_flat[idx_next]
                if v == v and v >= min_wet:
                    saturated += 1

                idx = idx_next
                steps += 1

                idx_fast = downstream_flat[idx_fast]
                if idx_fast < 0:
                    continue
                idx_fast = downstream_flat[idx_fast]
                if idx_fast >= 0 and idx == idx_fast:
                    break

                if steps >= n_cells:
                    break

            pdsl[fi] = 1.0 if steps == 0 else saturated / steps

        return pdsl.reshape((rows, cols))


def _compute_pdsl_local(
    d8_array,
    twi_array,
    mask_array=None,
    channel_mask=None,
    dem_array=None,
    progress_callback=None,
):
    if progress_callback is not None:
        progress_callback(46, "5.1 Preparing PDSL inputs...")

    rows, cols = twi_array.shape

    if mask_array is not None:
        valid = np.isfinite(twi_array) & (mask_array == 1)
    else:
        valid = np.isfinite(twi_array)

    wet_flat = normalise_percentile(twi_array, 5, 95).astype(np.float64, copy=False).ravel()
    valid_flat = valid.astype(np.bool_, copy=False).ravel()

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

    # Force channel cells to terminate downslope tracing, matching SAGA's
    # CHCIC::downslopeSaturatedLength (D8 flow direction set to NoData at
    # channel cells so hillslope paths stop once they reach the network).
    if channel_mask is not None:
        channel_flat = (np.asarray(channel_mask, dtype=bool) & valid).ravel()
        downstream_flat = downstream_flat.copy()
        downstream_flat[channel_flat] = -1

    if progress_callback is not None:
        progress_callback(51, "5.3 Tracing downslope saturated length... 0%")

    pdsl_arr = _pdsl_kernel(downstream_flat, wet_flat, valid_flat, rows, cols)

    if progress_callback is not None:
        progress_callback(57, "5.5 PDSL computation complete.")

    return np.where(valid, pdsl_arr, np.nan)


def compute_pdsl(
    d8_array,
    twi_array,
    mask_array=None,
    channel_mask=None,
    dem_array=None,
    progress_callback=None,
):
    """Percentage Downslope Saturated Length (PDSL) index of connectivity.

    Ported from SAGA-GIS's ``CHCIC::downslopeSaturatedLength``. For each
    valid cell, the D8 flow path is traced downstream - over a normalised
    wetness index grid - until it reaches a channel cell or leaves the
    valid area. The cell's score is the fraction of downstream path cells
    whose wetness is at least as high as the starting cell's own wetness.
    Cells with no downstream path (channel cells, or cells that immediately
    drain off the valid area) score 1.0.
    """
    return _compute_pdsl_local(
        d8_array, twi_array, mask_array, channel_mask, dem_array, progress_callback,
    )


def compute_twi(accum_array, slope_array, rainfall_scaled_array):
    """Rainfall-weighted topographic wetness index.

    ``ln(|A| * rain + 1) - ln(tan(slope) + 0.001)``, evaluated only where every
    input is finite so NoData does not leak into the trace.
    """
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

    return twi_array


def compute_connectivity_flow_path_trace(
    d8_array,
    twi_array,
    mask_array=None,
    channel_mask=None,
    dem_array=None,
    progress_callback=None,
):
    """SAGA-style connectivity: trace each cell's D8 flow path downstream."""
    return _compute_connectivity_flow_path_trace_local(
        d8_array,
        twi_array,
        mask_array,
        channel_mask,
        dem_array,
        progress_callback,
    )


def compute_network_connectivity(
    d8_array,
    accum_array,
    slope_array,
    rainfall_scaled_array,
    mask_array=None,
    channel_mask=None,
    dem_array=None,
    progress_callback=None,
    method="flow_path_trace",
):
    """Compute TWI then apply the selected connectivity solver.

    Parameters
    ----------
    method : {"flow_path_trace", "pdsl"}
        "flow_path_trace" replicates SAGA-style per-cell downstream tracing
        of minimum TWI (the Network Index). "pdsl" computes the Percentage
        Downslope Saturated Length index (SAGA
        ``CHCIC::downslopeSaturatedLength``).
    """
    if progress_callback is not None:
        progress_callback(46, "5.1 Preparing TWI inputs for connectivity...")

    twi_array = compute_twi(accum_array, slope_array, rainfall_scaled_array)
    method_name = str(method).strip().lower()

    if progress_callback is not None:
        progress_callback(48, "5.2 TWI prepared; running connectivity solver...")

    if method_name in {"pdsl", "downslope_saturated_length"}:
        return compute_pdsl(
            d8_array,
            twi_array,
            mask_array,
            channel_mask,
            dem_array,
            progress_callback,
        )

    return compute_connectivity_flow_path_trace(
        d8_array,
        twi_array,
        mask_array,
        channel_mask,
        dem_array,
        progress_callback,
    )
