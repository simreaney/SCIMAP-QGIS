"""Wetness-Connectivity Curve: scatter plot and dataset export.

Plots each valid cell's topographic wetness index against its connectivity
score, for the Network Index tool. Matplotlib is imported lazily, and only
when a plot is actually requested, since the plugin has no other dependency
on it.
"""

import csv

import numpy as np

# Reference dataviz palette (single-series scatter: sequential/categorical
# slot 1, light chart surface).
_MARKER_COLOUR = '#2a78d6'
_GRIDLINE_COLOUR = '#e1e0d9'
_AXIS_COLOUR = '#c3c2b7'
_SURFACE_COLOUR = '#fcfcfb'
_PRIMARY_INK = '#0b0b0b'
_SECONDARY_INK = '#52514e'
_MUTED_INK = '#898781'


def sample_wetness_connectivity(wetness_arr, connectivity_arr, mask_arr, max_points=50000, seed=0):
    """Pair up valid wetness/connectivity cells, subsampling large rasters.

    Returns ``(wetness_sample, connectivity_sample, n_valid_total)``. Above
    *max_points* valid cells, a random subset is drawn so the plot stays
    readable and the dataset stays a manageable file size; below it, every
    valid cell is kept.
    """
    valid = np.isfinite(wetness_arr) & np.isfinite(connectivity_arr)
    if mask_arr is not None:
        valid &= np.asarray(mask_arr, dtype=bool)

    wet = wetness_arr[valid].ravel()
    conn = connectivity_arr[valid].ravel()
    n_valid = wet.size

    if n_valid > max_points:
        rng = np.random.default_rng(seed)
        idx = rng.choice(n_valid, size=max_points, replace=False)
        idx.sort()
        wet = wet[idx]
        conn = conn[idx]

    return wet, conn, n_valid


def save_scatter_dataset(wetness_sample, connectivity_sample, csv_path):
    """Write the wetness/connectivity pairs behind the curve to a CSV file."""
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['wetness', 'connectivity'])
        writer.writerows(zip(wetness_sample.tolist(), connectivity_sample.tolist()))


def save_scatter_plot(wetness_sample, connectivity_sample, png_path):
    """Render the Wetness-Connectivity Curve scatter plot to a PNG file."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5), dpi=150)
    fig.patch.set_facecolor(_SURFACE_COLOUR)
    ax.set_facecolor(_SURFACE_COLOUR)

    ax.grid(True, color=_GRIDLINE_COLOUR, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(_AXIS_COLOUR)

    # Marker size/alpha traded down from the >=8px single-mark spec: with up
    # to tens of thousands of points, small semi-transparent dots read the
    # point cloud's density where large opaque markers would just merge into
    # a solid blob.
    ax.scatter(
        wetness_sample, connectivity_sample,
        s=6, alpha=0.35, color=_MARKER_COLOUR, edgecolors='none', zorder=2,
    )

    ax.set_title('Wetness-Connectivity Curve', color=_PRIMARY_INK, fontsize=13)
    ax.set_xlabel('Topographic wetness index', color=_SECONDARY_INK)
    ax.set_ylabel('Connectivity score', color=_SECONDARY_INK)
    ax.tick_params(colors=_MUTED_INK)
    ax.set_ylim(-0.02, 1.02)

    fig.tight_layout()
    fig.savefig(png_path, facecolor=_SURFACE_COLOUR)
    plt.close(fig)
