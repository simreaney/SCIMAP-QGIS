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


# ── SCIMAP-Fitted diagnostics ──────────────────────────────────────────
#
# Every plot below is a single series, so none carries a legend box: the title
# names what is being shown and colour carries no identity. The one exception
# is the error heatmap, which is a genuine continuous magnitude and so gets a
# sequential ramp and a scale legend.
#
# Note the research scripts colour their per-catchment error bars by value with
# a diverging blue-red ramp. That is wrong twice over: the quantity is a
# non-negative magnitude with no meaningful midpoint, and colouring nominal
# categories by value double-encodes the bar length in hue. One hue for every
# bar here.

#: Sequential blue, light -> dark, for continuous magnitude only.
_SEQUENTIAL_BLUE = (
    '#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7',
    '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b',
)

_BOX_FILL = '#cde2fb'
_REFERENCE_LINE = '#898781'


def _new_figure(width, height):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    fig.patch.set_facecolor(_SURFACE_COLOUR)
    _style_axes(ax)
    return plt, fig, ax


def _style_axes(ax, grid_axis='y'):
    """Recessive, solid hairline chrome, one shade off the surface."""
    ax.set_facecolor(_SURFACE_COLOUR)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color=_GRIDLINE_COLOUR, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for spine_name, spine in ax.spines.items():
        if spine_name in ('top', 'right'):
            spine.set_visible(False)
        else:
            spine.set_color(_AXIS_COLOUR)
            spine.set_linewidth(1)
    ax.tick_params(colors=_MUTED_INK, length=0)


def _finish(fig, png_path, plt):
    fig.tight_layout()
    fig.savefig(png_path, facecolor=_SURFACE_COLOUR)
    plt.close(fig)
    return png_path


def _style_boxes(artists):
    for box in artists['boxes']:
        box.set_facecolor(_BOX_FILL)
        box.set_edgecolor(_MARKER_COLOUR)
        box.set_linewidth(1)
    for part in ('whiskers', 'caps'):
        for line in artists[part]:
            line.set_color(_MARKER_COLOUR)
            line.set_linewidth(1)
    for line in artists['medians']:
        line.set_color(_PRIMARY_INK)
        line.set_linewidth(2)
    for flier in artists['fliers']:
        flier.set(marker='o', markersize=3, markerfacecolor=_MARKER_COLOUR,
                  markeredgecolor='none', alpha=0.4)


def save_weight_boxplot(weights, class_labels, png_path,
                        title='Fitted land-cover risk weights',
                        subtitle=None, ylabel='Risk weight'):
    """Distribution of each land-cover class's weight across the best sets.

    *weights* is (n_sets x n_classes) — normally the top-N sets, normalised by
    ``calibration.normalise_weight_rows`` so the boxes show the spread of the
    class *ratios*. Without that normalisation they would largely show the
    spread of an arbitrary overall scale, because the calibration objective is
    invariant to rescaling the whole vector.
    """
    weights = np.asarray(weights, dtype=np.float64)
    if weights.ndim != 2:
        raise ValueError("save_weight_boxplot expects a 2-D (sets x classes) array")

    plt, fig, ax = _new_figure(max(6.0, 0.9 * len(class_labels) + 2.0), 4.4)
    artists = ax.boxplot(
        [weights[:, i] for i in range(weights.shape[1])],
        patch_artist=True, widths=0.55, whis=(5, 95),
    )
    _style_boxes(artists)

    ax.set_xticks(range(1, len(class_labels) + 1))
    ax.set_xticklabels(class_labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel(ylabel, color=_SECONDARY_INK)
    # Reserve room for the subtitle rather than letting it land on the title.
    ax.set_title(title, color=_PRIMARY_INK, fontsize=13, loc='left',
                 pad=24 if subtitle else 12)
    if subtitle:
        ax.text(0.0, 1.015, subtitle, transform=ax.transAxes,
                color=_MUTED_INK, fontsize=9, va='bottom')
    return _finish(fig, png_path, plt)


def save_dotty_plot(weights, scores, class_labels, png_path,
                    title='Parameter weight against goodness of fit',
                    ylabel='Spearman rank correlation',
                    max_points=8000, seed=0, significance_lines=()):
    """The GLUE-style dotty plot: one small multiple per land-cover class.

    A class whose points form a clear ridge is *identifiable* — the data
    constrain its weight. A class whose points are a flat band is not, and its
    fitted weight should not be read as meaningful however tight the top-N
    boxplot looks.

    *significance_lines* is a sequence of ``(value, label)`` drawn across every
    panel, for marking the correlation a fit has to beat to be significant.
    They are dashed, unlike the solid hairline grid, because they are
    thresholds rather than chrome, and labelled once on the rightmost panel so
    the reader never decodes a line style from a legend.
    """
    weights = np.asarray(weights, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    n_classes = weights.shape[1]

    # A full ensemble is tens of thousands of points in a panel a couple of
    # inches wide, which paints a solid block and hides exactly the density
    # structure the plot exists to show. Subsample deterministically and keep
    # the markers small and faint so the cloud reads as density.
    if weights.shape[0] > max_points:
        rng = np.random.default_rng(seed)
        keep = np.sort(rng.choice(weights.shape[0], size=max_points, replace=False))
        weights = weights[keep]
        scores = scores[keep]

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        1, n_classes, figsize=(max(8.0, 1.7 * n_classes), 3.6),
        dpi=150, sharey=True,
    )
    fig.patch.set_facecolor(_SURFACE_COLOUR)
    axes = np.atleast_1d(axes)

    lines = [(value, label) for value, label in significance_lines
             if np.isfinite(value)]

    for index, ax in enumerate(axes):
        _style_axes(ax, grid_axis='both')
        ax.scatter(weights[:, index], scores, s=2, alpha=0.12,
                   color=_MARKER_COLOUR, edgecolors='none', zorder=2)
        for value, _label in lines:
            ax.axhline(value, color=_REFERENCE_LINE, linewidth=1,
                       linestyle='--', zorder=3)
        ax.set_xlabel(class_labels[index], color=_SECONDARY_INK, fontsize=9)
        ax.set_xlim(-0.03, 1.03)
        if index == 0:
            ax.set_ylabel(ylabel, color=_SECONDARY_INK)

    # Labelled once, on the rightmost panel, to avoid repeating them n times.
    for value, label in lines:
        axes[-1].text(0.97, value, label, transform=axes[-1].get_yaxis_transform(),
                      color=_MUTED_INK, fontsize=8, va='bottom', ha='right',
                      zorder=4)

    fig.suptitle(title, color=_PRIMARY_INK, fontsize=13, x=0.01, ha='left')
    return _finish(fig, png_path, plt)


def save_spearman_boxplot(scores_by_column, png_path,
                          title='Cross-validated skill by determinand',
                          ylabel='Spearman rank correlation',
                          significance_lines=()):
    """Spread of cross-validated scores, one box per determinand.

    *significance_lines* is a sequence of ``(value, label)``. These are drawn
    dashed, unlike the solid hairline grid, because they genuinely are
    thresholds rather than chrome — and they are labelled directly so the
    reader never has to decode a line style from a legend.
    """
    names = list(scores_by_column)
    data = [np.asarray(scores_by_column[name], dtype=np.float64) for name in names]
    data = [values[np.isfinite(values)] for values in data]

    plt, fig, ax = _new_figure(max(5.0, 1.3 * len(names) + 2.5), 4.4)
    artists = ax.boxplot(data, patch_artist=True, widths=0.5, whis=(5, 95))
    _style_boxes(artists)

    # Labels sit just inside the right edge: outside it they are clipped by
    # tight_layout, and a legend would make the reader decode a line style.
    for value, label in significance_lines:
        if not np.isfinite(value):
            continue
        ax.axhline(value, color=_REFERENCE_LINE, linewidth=1, linestyle='--', zorder=1)
        ax.text(0.995, value, label, transform=ax.get_yaxis_transform(),
                color=_MUTED_INK, fontsize=8, va='bottom', ha='right')

    ax.set_xticks(range(1, len(names) + 1))
    ax.set_xticklabels(names, rotation=20, ha='right', fontsize=9)
    ax.set_ylabel(ylabel, color=_SECONDARY_INK)
    ax.set_title(title, color=_PRIMARY_INK, fontsize=13, loc='left')
    return _finish(fig, png_path, plt)


def save_error_bar_plot(labels, values, png_path,
                        title='Held-out prediction error by site',
                        ylabel='Standardised absolute error'):
    """One bar per held-out site. One series, so one colour for every bar."""
    values = np.asarray(values, dtype=np.float64)
    plt, fig, ax = _new_figure(max(6.0, 0.32 * len(labels) + 2.0), 4.0)

    positions = np.arange(len(labels))
    ax.bar(positions, values, width=0.78, color=_MARKER_COLOUR,
           edgecolor='none', zorder=2)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_ylabel(ylabel, color=_SECONDARY_INK)
    ax.set_title(title, color=_PRIMARY_INK, fontsize=13, loc='left')
    return _finish(fig, png_path, plt)


def save_error_heatmap(matrix, labels, png_path,
                       title='Held-out prediction error by site pair',
                       colourbar_label='Standardised absolute error'):
    """Leave-two-out error as a matrix. Continuous magnitude: sequential ramp."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    matrix = np.asarray(matrix, dtype=np.float64)
    colourmap = LinearSegmentedColormap.from_list(
        'scimap_sequential_blue', list(_SEQUENTIAL_BLUE))
    colourmap.set_bad(_SURFACE_COLOUR)

    size = max(5.0, 0.26 * len(labels) + 2.5)
    fig, ax = plt.subplots(figsize=(size, size * 0.85), dpi=150)
    fig.patch.set_facecolor(_SURFACE_COLOUR)
    _style_axes(ax, grid_axis=None)

    image = ax.imshow(np.ma.masked_invalid(matrix), cmap=colourmap,
                      interpolation='nearest', aspect='auto')

    ax.set_xticks(np.arange(len(labels)))
    ax.set_yticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_title(title, color=_PRIMARY_INK, fontsize=13, loc='left')

    colourbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
    colourbar.set_label(colourbar_label, color=_SECONDARY_INK, fontsize=9)
    colourbar.ax.tick_params(colors=_MUTED_INK, length=0)
    colourbar.outline.set_edgecolor(_AXIS_COLOUR)
    return _finish(fig, png_path, plt)


def save_histogram(values, png_path, title, xlabel, bins=40):
    """Distribution of one quantity across the weight-set ensemble."""
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]

    plt, fig, ax = _new_figure(6.5, 4.0)
    if values.size:
        ax.hist(values, bins=bins, color=_MARKER_COLOUR, edgecolor='none', zorder=2)
    ax.set_xlabel(xlabel, color=_SECONDARY_INK)
    ax.set_ylabel('Count', color=_SECONDARY_INK)
    ax.set_title(title, color=_PRIMARY_INK, fontsize=13, loc='left')
    return _finish(fig, png_path, plt)
