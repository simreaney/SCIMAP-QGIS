"""SCIMAP-Fitted weight calibration: the pure-NumPy core.

Ported from ``SCIMAP-Fitted-Step3.py`` and
``SCIMAP-Fitted-Step3-CrossValidation.py``.

SCIMAP Standard takes its land-cover risk weights as an assumption. SCIMAP
Fitted infers them instead: for every monitoring site it summarises modelled
risk per land-cover class over that site's upstream catchment, then searches
weight space for the vector whose predicted risk ranks the sites in the same
order the observations do.

For one site *i* and land-cover class *c*, with ``B[i, c]`` the class area
times the mean of (connectivity x normalised erosion) inside site *i*'s
catchment, and ``D[i]`` a rainfall-weighted discharge proxy at its outlet::

    y_hat_i(w) = (sum_c w_c * B[i, c]) / D[i]

Weight vectors are drawn by Latin hypercube and scored by Spearman rank
correlation against the observations. Pearson, R^2 and p-values are recorded
as diagnostics only -- R^2 in particular compares an arbitrary-scale index
against a concentration, so it is not a usable objective.

**The objective is invariant to positive rescaling of w.** Because the
prediction is linear in ``w`` and correlation is scale-free, ``w`` and ``3w``
score identically, so the effective parameter space is the ratios between
classes, not the absolute magnitudes. See :func:`normalise_weight_rows`.

This module deliberately depends on NumPy alone -- no GDAL, no QGIS, no
pandas, no scipy -- so that an identical copy can live in the web application
and be checked against this one by a parity test. For the same reason it holds
no land-cover labels: callers pass those in.

**Threading contract.** ``progress`` callables passed in here are only ever
invoked from the calling thread, never from a worker. QGIS's
``QgsProcessingFeedback`` is wired to dialog widgets and is not thread-safe.
"""

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations

import numpy as np

#: Observations equal to this are treated as missing, matching the research
#: scripts and ``core/raster_io.py``'s NODATA.
OBSERVATION_SENTINEL = -9999.0

#: A determinand needs at least this many valid observations to be calibrated
#: against; below it a correlation is meaningless.
MIN_OBSERVATIONS = 3

RANK_METRICS = ('mean_spearman', 'mean_pearson', 'mean_r2')


class DesignMatrix:
    """Per-site, per-class risk loads with the dilution already divided out."""

    __slots__ = ('site_ids', 'classes', 'base', 'dilution')

    def __init__(self, site_ids, classes, base, dilution):
        self.site_ids = site_ids
        self.classes = classes
        #: (n_sites x n_classes), **already divided by dilution**.
        self.base = base
        #: (n_sites,) the raw dilution factors, kept for reporting.
        self.dilution = dilution

    @property
    def n_sites(self):
        return len(self.site_ids)

    @property
    def n_classes(self):
        return len(self.classes)


class CalibrationResult:
    """Metrics for every sampled weight set, plus the ranking over them."""

    __slots__ = (
        'weights', 'obs_columns', 'n_obs_by_column',
        'spearman', 'pearson', 'r2', 'spearman_p', 'pearson_p',
        'mean_spearman', 'mean_pearson', 'mean_r2',
        'rank_by', 'rank_score', 'order',
    )

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def n_sets(self):
        return self.weights.shape[0]

    def top_indices(self, top_n, column=None):
        """Indices of the best *top_n* sets, overall or for one determinand."""
        scores = self.rank_score if column is None else self.spearman[column]
        return top_n_indices(scores, top_n)


class CrossValidationResult:
    """Leave-k-out results: parameter-set ranking and out-of-sample skill."""

    __slots__ = (
        'obs_columns', 'k_out', 'folds', 'n_folds_exhaustive', 'sampled',
        'mean_spearman_by_column', 'mean_spearman', 'fold_count',
        'predictions', 'observed', 'prediction_counts',
    )

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# ── Sampling ────────────────────────────────────────────────────────────

def latin_hypercube(n_samples, n_dims, rng):
    """Draw a Latin hypercube sample in the unit cube.

    Each dimension is split into *n_samples* equal strata with exactly one
    sample in each, so the space is covered far more evenly than by
    independent uniform draws at the same sample count.
    """
    if n_samples < 1 or n_dims < 1:
        raise ValueError("latin_hypercube needs at least one sample and one dimension")
    jitter = rng.random((n_samples, n_dims))
    sample = np.empty_like(jitter)
    for dim in range(n_dims):
        sample[:, dim] = (rng.permutation(n_samples) + jitter[:, dim]) / n_samples
    return sample


def sample_weights(n_samples, classes, bounds=None, seed=42,
                   pinned_class=None, pinned_weight=None):
    """Latin-hypercube sample one weight per land-cover class.

    *bounds* is an optional ``{class_id: (lower, upper)}`` mapping; classes
    absent from it default to [0, 1]. *pinned_class* is held at
    *pinned_weight* and excluded from the sample, which both removes a free
    dimension and fixes the scale degeneracy noted in the module docstring.

    Returns ``(weights, sampled_classes)`` where ``weights`` is
    (n_samples x len(classes)) in the order of *classes*.
    """
    classes = list(classes)
    n_classes = len(classes)
    if n_classes == 0:
        raise ValueError("sample_weights needs at least one land-cover class")

    bounds = bounds or {}
    lower = np.array([bounds.get(c, (0.0, 1.0))[0] for c in classes], dtype=np.float64)
    upper = np.array([bounds.get(c, (0.0, 1.0))[1] for c in classes], dtype=np.float64)
    if np.any(upper <= lower):
        bad = [c for c, lo, hi in zip(classes, lower, upper) if hi <= lo]
        raise ValueError(f"Weight bounds are not increasing for classes: {bad}")

    adjustable = [i for i, c in enumerate(classes) if c != pinned_class]
    if not adjustable:
        raise ValueError("Every land-cover class is pinned; nothing to calibrate")

    rng = np.random.default_rng(seed)
    unit = latin_hypercube(n_samples, len(adjustable), rng)

    weights = np.zeros((n_samples, n_classes), dtype=np.float64)
    idx = np.array(adjustable)
    weights[:, idx] = lower[idx] + (upper[idx] - lower[idx]) * unit

    if pinned_class is not None and pinned_class in classes:
        weights[:, classes.index(pinned_class)] = float(pinned_weight)

    return weights, [classes[i] for i in adjustable]


def normalise_weight_rows(weights):
    """Rescale each weight set so its largest component is 1.

    Purely presentational, and provably harmless: the calibration objective is
    a correlation over a prediction linear in ``w``, so ``w`` and ``alpha*w``
    score identically; and SCIMAP Standard applies its own 5th/95th percentile
    stretch after multiplying by the weights, so a rescaled set produces an
    identical risk map too.

    It matters because without it the top-N weight *boxplots* are misleading.
    The set ``{alpha*w}`` is one point in the effective parameter space but
    appears as many distinct rows, so the boxplot whiskers show the spread of
    the arbitrary scale rather than the real uncertainty in the class ratios.

    Rows that are entirely zero or non-finite are returned unchanged.
    """
    weights = np.asarray(weights, dtype=np.float64)
    peak = np.nanmax(np.abs(weights), axis=1, keepdims=True)
    safe = np.isfinite(peak) & (peak > 0)
    return np.where(safe, weights / np.where(safe, peak, 1.0), weights)


# ── Rank and correlation statistics ─────────────────────────────────────

def average_ranks(values, axis=0):
    """Rank along *axis*, giving tied values their average rank.

    Replaces ``scipy.stats.rankdata``. The research scripts use *ordinal*
    ranks for predictions (justified on the grounds that continuous Monte
    Carlo output has no ties) but *average* ranks for the observations.
    Averaging both is identical when there are no ties and correct when there
    are -- and observations very much do have ties, both from repeated
    measurements and from values reported at a detection limit.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        return average_ranks(array[:, None], axis=0)[:, 0]
    if axis != 0:
        return np.moveaxis(average_ranks(np.moveaxis(array, axis, 0), 0), 0, axis)

    n_rows = array.shape[0]
    order = np.argsort(array, axis=0, kind='mergesort')
    ordered = np.take_along_axis(array, order, axis=0)

    starts_group = np.empty(ordered.shape, dtype=bool)
    starts_group[0] = True
    starts_group[1:] = ordered[1:] != ordered[:-1]
    ends_group = np.empty(ordered.shape, dtype=bool)
    ends_group[-1] = True
    ends_group[:-1] = starts_group[1:]

    positions = np.broadcast_to(
        np.arange(n_rows, dtype=np.float64)[:, None], ordered.shape)
    group_start = np.maximum.accumulate(
        np.where(starts_group, positions, -1.0), axis=0)
    group_end = np.minimum.accumulate(
        np.where(ends_group, positions, float(n_rows))[::-1], axis=0)[::-1]

    ordered_ranks = (group_start + group_end) / 2.0 + 1.0
    ranks = np.empty(ordered.shape, dtype=np.float64)
    np.put_along_axis(ranks, order, ordered_ranks, axis=0)
    return ranks


def pearson_by_column(predictions, observed):
    """Pearson r between each column of *predictions* and *observed*.

    *predictions* is (n_obs x n_sets); the result is (n_sets,). Columns with
    no variance yield NaN rather than 0, so that a degenerate weight set is
    excluded from ``nanmean``/``nanargmax`` instead of being ranked as merely
    uncorrelated.
    """
    predictions = np.asarray(predictions, dtype=np.float64)
    observed = np.asarray(observed, dtype=np.float64)

    centred_pred = predictions - predictions.mean(axis=0)
    centred_obs = observed - observed.mean()

    numerator = centred_pred.T @ centred_obs
    pred_ss = np.einsum('ij,ij->j', centred_pred, centred_pred)
    obs_ss = float(centred_obs @ centred_obs)

    denominator = np.sqrt(pred_ss * obs_ss)
    with np.errstate(invalid='ignore', divide='ignore'):
        result = np.where(denominator > 0, numerator / denominator, np.nan)
    return np.clip(result, -1.0, 1.0)


def spearman_by_column(predictions, observed, batch_size=200000):
    """Spearman rank correlation between each prediction column and *observed*.

    Batched over columns because the rank step allocates a float64 array the
    size of its input; at 100k weight sets an unbatched pass is a needless
    multi-hundred-megabyte spike.
    """
    predictions = np.asarray(predictions, dtype=np.float64)
    observed_ranks = average_ranks(np.asarray(observed, dtype=np.float64))

    n_sets = predictions.shape[1]
    result = np.empty(n_sets, dtype=np.float64)
    step = max(1, int(batch_size))
    for start in range(0, n_sets, step):
        stop = min(start + step, n_sets)
        block_ranks = average_ranks(predictions[:, start:stop])
        result[start:stop] = pearson_by_column(block_ranks, observed_ranks)
    return result


def r2_by_column(predictions, observed):
    """Coefficient of determination, without any rescaling.

    Diagnostic only. The prediction is an arbitrary-scale index and the
    observation is a concentration, so this is dominated by the scale mismatch
    and is typically large and negative. It is reported because the research
    scripts report it, not because it ranks anything usefully.
    """
    predictions = np.asarray(predictions, dtype=np.float64)
    observed = np.asarray(observed, dtype=np.float64)

    total_ss = float(((observed - observed.mean()) ** 2).sum())
    if total_ss <= 0:
        return np.full(predictions.shape[1], np.nan)
    residual_ss = np.einsum(
        'ij,ij->j',
        predictions - observed[:, None],
        predictions - observed[:, None],
    )
    return 1.0 - residual_ss / total_ss


# ── Student's t survival function (vendored; no scipy) ──────────────────

def _beta_continued_fraction(a, b, x, max_iterations=300, tolerance=1e-15):
    """Lentz's modified continued fraction for the incomplete beta function."""
    x = np.asarray(x, dtype=np.float64)
    tiny = 1e-300

    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = np.ones_like(x)
    d = 1.0 - qab * x / qap
    d = np.where(np.abs(d) < tiny, tiny, d)
    d = 1.0 / d
    h = d.copy()

    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        for numerator in (
            m * (b - m) * x / ((qam + m2) * (a + m2)),
            -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)),
        ):
            d = 1.0 + numerator * d
            d = np.where(np.abs(d) < tiny, tiny, d)
            c = 1.0 + numerator / c
            c = np.where(np.abs(c) < tiny, tiny, c)
            d = 1.0 / d
            delta = d * c
            h = h * delta
        if np.all(np.abs(delta - 1.0) < tolerance):
            break
    return h


def regularised_incomplete_beta(a, b, x):
    """``I_x(a, b)``, vectorised over *x* with scalar *a* and *b*."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.nan, dtype=np.float64)

    finite = np.isfinite(x)
    out[finite & (x <= 0.0)] = 0.0
    out[finite & (x >= 1.0)] = 1.0

    interior = finite & (x > 0.0) & (x < 1.0)
    if not np.any(interior):
        return out

    xi = x[interior]
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * np.log(xi) + b * np.log1p(-xi)
    )
    front = np.exp(log_front)

    # The continued fraction converges quickly only on one side of this
    # point; use the symmetry I_x(a,b) = 1 - I_(1-x)(b,a) for the other.
    swap = xi >= (a + 1.0) / (a + b + 2.0)
    values = np.empty_like(xi)
    if np.any(~swap):
        values[~swap] = (
            front[~swap] * _beta_continued_fraction(a, b, xi[~swap]) / a)
    if np.any(swap):
        values[swap] = 1.0 - (
            front[swap] * _beta_continued_fraction(b, a, 1.0 - xi[swap]) / b)

    out[interior] = values
    return out


def student_t_sf(t_values, df):
    """Upper-tail probability ``P(T > t)`` for Student's t with *df* degrees.

    Vendored rather than taken from ``scipy.stats.t`` because scipy is not a
    dependency of either the QGIS plugin or the web application, and because
    one implementation keeps the plugin and web app copies of this module
    bit-identical regardless of what happens to be installed. Checked against
    scipy in the test suite where scipy is available.
    """
    t_values = np.asarray(t_values, dtype=np.float64)
    if df <= 0:
        return np.full(t_values.shape, np.nan)
    with np.errstate(invalid='ignore', divide='ignore'):
        x = df / (df + t_values * t_values)
    half = 0.5 * regularised_incomplete_beta(df / 2.0, 0.5, x)
    return np.where(t_values >= 0.0, half, 1.0 - half)


def correlation_p_values(correlations, n_obs):
    """Two-sided p-values for a correlation coefficient under the null r = 0."""
    correlations = np.asarray(correlations, dtype=np.float64)
    df = n_obs - 2
    if df < 1:
        return np.full(correlations.shape, np.nan)

    limit = 1.0 - 1e-12
    clipped = np.clip(correlations, -limit, limit)
    with np.errstate(invalid='ignore', divide='ignore'):
        t_stat = clipped * np.sqrt(df / (1.0 - clipped * clipped))
    return np.clip(2.0 * student_t_sf(np.abs(t_stat), df), 0.0, 1.0)


def critical_correlation(n_obs, alpha=0.05):
    """The smallest |r| significant at *alpha* for *n_obs* observations.

    Used for the reference lines on the cross-validation boxplots, where a
    threshold is more readable than per-point p-values.
    """
    df = n_obs - 2
    if df < 1:
        return float('nan')
    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if float(correlation_p_values(np.array([mid]), n_obs)[0]) > alpha:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── Design matrix ───────────────────────────────────────────────────────

def _sorted_ids(ids):
    """Sort site identifiers, tolerating a mix of ints and strings."""
    try:
        return sorted(ids)
    except TypeError:
        return sorted(ids, key=str)


def _as_float(value):
    if value is None or value == '':
        return float('nan')
    try:
        return float(value)
    except (TypeError, ValueError):
        return float('nan')


def build_design_matrix(rows, value_key='mean_connectivity_x_erosion',
                        area_key='area_m2', class_key='land_cover_class',
                        site_key='site_id', dilution_key='rainfall_dilution'):
    """Assemble the per-site, per-class risk loads from long-format rows.

    *rows* is an iterable of mappings, one per (site, land-cover class), as
    produced by the catchment-statistics stage. Areas for a repeated pair are
    summed and values averaged, matching the research scripts' pivot.

    ``B[i, c] = area_ic * mean_ic`` is the total risk load of class *c* in site
    *i*'s catchment before weighting. It is then divided by that site's
    dilution factor.

    Dividing here rather than at prediction time is deliberate. Step 3 of the
    research scripts divides the *predictions* while the cross-validation
    script divides the *base matrix*; the two are algebraically identical
    (``(B @ w) / D == (B / D) @ w``) but keeping both is how they drift apart.
    A missing, non-positive or non-finite dilution falls back to 1.0.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("build_design_matrix received no rows")

    site_ids = _sorted_ids({row[site_key] for row in rows})
    classes = sorted({int(row[class_key]) for row in rows})
    site_index = {site: i for i, site in enumerate(site_ids)}
    class_index = {cls: j for j, cls in enumerate(classes)}

    shape = (len(site_ids), len(classes))
    area_sum = np.zeros(shape, dtype=np.float64)
    value_sum = np.zeros(shape, dtype=np.float64)
    value_count = np.zeros(shape, dtype=np.float64)
    dilution_sum = np.zeros(len(site_ids), dtype=np.float64)
    dilution_count = np.zeros(len(site_ids), dtype=np.float64)

    for row in rows:
        i = site_index[row[site_key]]
        j = class_index[int(row[class_key])]

        area = _as_float(row.get(area_key))
        if np.isfinite(area):
            area_sum[i, j] += area

        value = _as_float(row.get(value_key))
        if np.isfinite(value):
            value_sum[i, j] += value
            value_count[i, j] += 1.0

        dilution = _as_float(row.get(dilution_key))
        if np.isfinite(dilution) and dilution > 0:
            dilution_sum[i] += dilution
            dilution_count[i] += 1.0

    value_mean = np.divide(
        value_sum, value_count,
        out=np.zeros(shape, dtype=np.float64), where=value_count > 0,
    )
    base = area_sum * value_mean

    dilution = np.divide(
        dilution_sum, dilution_count,
        out=np.ones(len(site_ids), dtype=np.float64), where=dilution_count > 0,
    )
    dilution = np.where(np.isfinite(dilution) & (dilution > 0), dilution, 1.0)

    return DesignMatrix(site_ids, classes, base / dilution[:, None], dilution)


# ── Calibration ─────────────────────────────────────────────────────────

def _valid_observation_mask(values):
    values = np.asarray(values, dtype=np.float64)
    return np.isfinite(values) & (values != OBSERVATION_SENTINEL)


def top_n_indices(scores, top_n):
    """Indices of the *top_n* highest scores, best first, NaN last."""
    scores = np.asarray(scores, dtype=np.float64)
    ordered = np.argsort(
        np.where(np.isfinite(scores), -scores, np.inf), kind='stable')
    return ordered[:max(0, int(top_n))]


def evaluate_parameter_sets(base, weights, observations, batch_size=200000,
                            rank_by='mean_spearman', progress=None):
    """Score every weight set against every determinand.

    *observations* maps a determinand name to one value per site, in the same
    order as the design matrix's ``site_ids``. Each determinand is scored
    independently and then averaged, so a weight set that suits phosphorus but
    not nitrate is ranked on its average.
    """
    if rank_by not in RANK_METRICS:
        raise ValueError(f"rank_by must be one of {RANK_METRICS}, got {rank_by!r}")

    base = np.asarray(base, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    if base.shape[1] != weights.shape[1]:
        raise ValueError(
            f"Design matrix has {base.shape[1]} classes but the weight sets "
            f"have {weights.shape[1]}."
        )

    spearman, pearson, r2 = {}, {}, {}
    spearman_p, pearson_p, n_obs_by_column = {}, {}, {}
    usable_columns = []

    names = list(observations)
    for position, name in enumerate(names):
        valid = _valid_observation_mask(observations[name])
        n_valid = int(valid.sum())
        if n_valid < MIN_OBSERVATIONS:
            continue

        observed = np.asarray(observations[name], dtype=np.float64)[valid]
        predicted = base[valid] @ weights.T

        spearman[name] = spearman_by_column(predicted, observed, batch_size)
        pearson[name] = pearson_by_column(predicted, observed)
        r2[name] = r2_by_column(predicted, observed)
        spearman_p[name] = correlation_p_values(spearman[name], n_valid)
        pearson_p[name] = correlation_p_values(pearson[name], n_valid)
        n_obs_by_column[name] = n_valid
        usable_columns.append(name)

        if progress is not None:
            progress((position + 1) / len(names), f"Scored {name}")

    if not usable_columns:
        raise ValueError(
            "No determinand had at least "
            f"{MIN_OBSERVATIONS} valid observations to calibrate against."
        )

    def _mean_over_columns(store):
        stacked = np.vstack([store[name] for name in usable_columns])
        with np.errstate(invalid='ignore'):
            return np.nanmean(stacked, axis=0)

    mean_spearman = _mean_over_columns(spearman)
    mean_pearson = _mean_over_columns(pearson)
    mean_r2 = _mean_over_columns(r2)

    rank_score = {
        'mean_spearman': mean_spearman,
        'mean_pearson': mean_pearson,
        'mean_r2': mean_r2,
    }[rank_by]

    return CalibrationResult(
        weights=weights,
        obs_columns=usable_columns,
        n_obs_by_column=n_obs_by_column,
        spearman=spearman,
        pearson=pearson,
        r2=r2,
        spearman_p=spearman_p,
        pearson_p=pearson_p,
        mean_spearman=mean_spearman,
        mean_pearson=mean_pearson,
        mean_r2=mean_r2,
        rank_by=rank_by,
        rank_score=rank_score,
        order=top_n_indices(rank_score, weights.shape[0]),
    )


def overfitting_risk(n_sites, n_classes):
    """Observations per free parameter, and a plain-language risk band.

    With seven land-cover classes and thirty sites there are roughly four
    observations per parameter, which is few enough that a high correlation
    can easily be an artefact of the search rather than a real signal. This is
    what the cross-validation exists to test.
    """
    if n_classes <= 0:
        return float('nan'), 'UNKNOWN'
    ratio = n_sites / n_classes
    if ratio < 10:
        return ratio, 'HIGH'
    if ratio < 15:
        return ratio, 'MODERATE'
    return ratio, 'LOW'


# ── Cross-validation ────────────────────────────────────────────────────

def leave_k_out_folds(n_sites, k, max_folds=None, seed=None):
    """Build the held-out index sets for leave-k-out cross-validation.

    Returns ``(folds, n_exhaustive, sampled)``. When the exhaustive set
    ``C(n_sites, k)`` exceeds *max_folds*, that many distinct folds are drawn
    uniformly at random instead and *sampled* is True -- bounded Monte-Carlo
    cross-validation, which is statistically sound and, given *seed*,
    reproducible. The exhaustive count is returned either way so callers can
    say honestly what was actually run: C(30, 5) is 142,506 folds, which is
    hours of work that nobody asked for by ticking a box.
    """
    if k < 1 or k >= n_sites:
        raise ValueError(
            f"k_out must be between 1 and {n_sites - 1} for {n_sites} sites, got {k}")

    n_exhaustive = math.comb(n_sites, k)
    if max_folds is None or n_exhaustive <= max_folds:
        return list(combinations(range(n_sites), k)), n_exhaustive, False

    rng = np.random.default_rng(seed)
    chosen = set()
    target = int(max_folds)
    # Rejection sampling: the target is far below the exhaustive count here
    # (that is why we are in this branch), so collisions are rare.
    while len(chosen) < target:
        chosen.add(tuple(sorted(rng.choice(n_sites, size=k, replace=False).tolist())))
    return sorted(chosen), n_exhaustive, True


def _cross_validate_chunk(base, weights, prepared, fold_chunk, batch_size):
    """Score one chunk of folds. Runs on a worker thread: no callbacks here."""
    n_sites = base.shape[0]
    n_sets = weights.shape[0]
    partial = {}

    for name, (values, valid) in prepared.items():
        partial[name] = {
            'score_sum': np.zeros(n_sets, dtype=np.float64),
            'score_count': np.zeros(n_sets, dtype=np.float64),
            'pred_sum': np.zeros(n_sites, dtype=np.float64),
            'pred_count': np.zeros(n_sites, dtype=np.float64),
        }

    for fold in fold_chunk:
        test_mask = np.zeros(n_sites, dtype=bool)
        test_mask[list(fold)] = True
        train_mask = ~test_mask

        for name, (values, valid) in prepared.items():
            train_valid = train_mask & valid
            if int(train_valid.sum()) < MIN_OBSERVATIONS:
                continue

            scores = spearman_by_column(
                base[train_valid] @ weights.T, values[train_valid], batch_size)

            finite = np.isfinite(scores)
            if not finite.any():
                continue
            store = partial[name]
            store['score_sum'][finite] += scores[finite]
            store['score_count'][finite] += 1.0

            # The honest out-of-sample number: pick the best weights using
            # only the training sites, then apply them to the held-out ones.
            best = int(np.argmax(np.where(finite, scores, -np.inf)))
            test_valid = test_mask & valid
            if test_valid.any():
                store['pred_sum'][test_valid] += base[test_valid] @ weights[best]
                store['pred_count'][test_valid] += 1.0

    return partial


def cross_validate(base, weights, observations, folds, batch_size=200000,
                   threads=1, progress=None):
    """Leave-k-out cross-validation over a fixed set of weight vectors.

    Two different things come out of this, and they answer different questions:

    * ``mean_spearman_by_column`` ranks each *weight set* by its average
      training-fold score. This is the ranking to trust when choosing weights,
      because it does not reward a set that happens to fit one arbitrary split.
    * ``predictions`` holds genuinely out-of-sample predictions: for each fold
      the best training-fold weights are applied to the held-out sites. Scoring
      these against the observations says whether the calibration generalises
      at all, which with roughly four observations per free parameter is the
      question that matters.

    A site held out by many folds gets **the average** of its out-of-sample
    predictions. The research scripts keep only whichever fold happened to come
    last, so with k=2 a site appearing in 29 folds reported one arbitrary one.

    Reusing the same *weights* the full calibration used is deliberate and is
    why this is not a separate tool: comparing a cross-validated score against
    a calibration score is only meaningful if both searched the identical
    sample.
    """
    base = np.asarray(base, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    folds = list(folds)
    if not folds:
        raise ValueError("cross_validate received no folds")

    prepared = {}
    for name in observations:
        values = np.asarray(observations[name], dtype=np.float64)
        valid = _valid_observation_mask(values)
        if int(valid.sum()) >= MIN_OBSERVATIONS:
            prepared[name] = (values, valid)
    if not prepared:
        raise ValueError(
            "No determinand had enough valid observations to cross-validate.")

    n_sites, n_sets = base.shape[0], weights.shape[0]
    totals = {
        name: {
            'score_sum': np.zeros(n_sets, dtype=np.float64),
            'score_count': np.zeros(n_sets, dtype=np.float64),
            'pred_sum': np.zeros(n_sites, dtype=np.float64),
            'pred_count': np.zeros(n_sites, dtype=np.float64),
        }
        for name in prepared
    }

    # Small chunks keep cancellation latency low: the caller only regains
    # control between merges.
    chunk_size = max(1, min(8, math.ceil(len(folds) / max(1, threads))))
    chunks = [folds[i:i + chunk_size] for i in range(0, len(folds), chunk_size)]

    def _merge(partial):
        for name, store in partial.items():
            for key, value in store.items():
                totals[name][key] += value

    done = 0
    if threads and threads > 1 and len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=int(threads)) as executor:
            futures = {
                executor.submit(
                    _cross_validate_chunk, base, weights, prepared, chunk, batch_size,
                ): len(chunk)
                for chunk in chunks
            }
            for future in as_completed(futures):
                # Merging and reporting both happen here, on the calling
                # thread, because `progress` may touch UI state.
                _merge(future.result())
                done += futures[future]
                if progress is not None:
                    progress(done / len(folds), f"{done}/{len(folds)} folds")
    else:
        for chunk in chunks:
            _merge(_cross_validate_chunk(base, weights, prepared, chunk, batch_size))
            done += len(chunk)
            if progress is not None:
                progress(done / len(folds), f"{done}/{len(folds)} folds")

    mean_by_column, predictions, observed, counts = {}, {}, {}, {}
    for name, store in totals.items():
        counted = store['score_count'] > 0
        mean_by_column[name] = np.where(
            counted,
            np.divide(store['score_sum'], store['score_count'],
                      out=np.zeros(n_sets), where=counted),
            np.nan,
        )
        predicted_any = store['pred_count'] > 0
        predictions[name] = np.where(
            predicted_any,
            np.divide(store['pred_sum'], store['pred_count'],
                      out=np.zeros(n_sites), where=predicted_any),
            np.nan,
        )
        counts[name] = store['pred_count']
        observed[name] = np.where(prepared[name][1], prepared[name][0], np.nan)

    with np.errstate(invalid='ignore'):
        mean_spearman = np.nanmean(
            np.vstack([mean_by_column[name] for name in prepared]), axis=0)

    return CrossValidationResult(
        obs_columns=list(prepared),
        k_out=len(folds[0]),
        folds=folds,
        n_folds_exhaustive=None,
        sampled=None,
        mean_spearman_by_column=mean_by_column,
        mean_spearman=mean_spearman,
        fold_count=len(folds),
        predictions=predictions,
        observed=observed,
        prediction_counts=counts,
    )


def _standardise(values):
    spread = np.std(values)
    if not np.isfinite(spread) or spread <= 0:
        return np.zeros_like(values)
    return (values - np.mean(values)) / spread


def out_of_sample_scores(cv_result):
    """Score the pooled held-out predictions against the observations.

    Spearman is the headline number and the only one to quote on its own: it
    is scale-free, so it measures whether the calibration ranks unseen sites
    correctly, which is the question cross-validation exists to answer.

    R^2 and MAE are computed **after standardising both series** to zero mean
    and unit variance. The research scripts compare them raw, but the
    prediction is an arbitrary-scale index (units of area x risk / discharge
    proxy) and the observation is a concentration, so a raw comparison is
    dominated by the scale mismatch and ranks sites by how far apart the two
    scales happen to be rather than by skill.
    """
    scores = {}
    for name in cv_result.obs_columns:
        predicted = np.asarray(cv_result.predictions[name], dtype=np.float64)
        observed = np.asarray(cv_result.observed[name], dtype=np.float64)
        valid = np.isfinite(predicted) & np.isfinite(observed)
        n_valid = int(valid.sum())

        entry = {
            'n_valid': n_valid,
            'spearman': float('nan'), 'spearman_p': float('nan'),
            'pearson': float('nan'), 'pearson_p': float('nan'),
            'r2': float('nan'), 'mae': float('nan'),
        }
        if n_valid >= MIN_OBSERVATIONS:
            p, o = predicted[valid], observed[valid]
            spearman = float(spearman_by_column(p[:, None], o)[0])
            pearson = float(pearson_by_column(p[:, None], o)[0])
            zp, zo = _standardise(p), _standardise(o)
            entry.update({
                'spearman': spearman,
                'spearman_p': float(correlation_p_values(
                    np.array([spearman]), n_valid)[0]),
                'pearson': pearson,
                'pearson_p': float(correlation_p_values(
                    np.array([pearson]), n_valid)[0]),
                'r2': float(r2_by_column(zp[:, None], zo)[0]),
                'mae': float(np.mean(np.abs(zp - zo))),
            })
        scores[name] = entry
    return scores
